"""===========================================================================================================================================================================
Title:        ZEN-GARDEN
Created:      October-2021
Authors:      Alissa Ganter (aganter@ethz.ch)
Organization: Laboratory of Reliability and Risk Engineering, ETH Zurich

Description:  Class is defining the postprocessing of the results.
              The class takes as inputs the optimization problem (model) and the system configurations (system).
              The class contains methods to read the results and save them in a result dictionary (resultDict).
==========================================================================================================================================================================="""
import logging
import sys

import numpy as np
import pyomo.environ as pe
import pandas as pd
import pathlib
import shutil
import h5py
import json
import zlib
import os

from .. import utils
from ..utils import RedirectStdStreams


class Postprocess:

    def __init__(self, model, scenarios, model_name, subfolder=None, scenario_name=None, save_opt=False):
        """postprocessing of the results of the optimization
        :param model: optimization model
        :param model_name: The name of the model used to name the output folder
        :param subfolder: The subfolder used for the results
        :param scenario_name: The name of the current scenario
        :param save_opt: Save the dict of the opt as gszip
        """
        logging.info("Postprocess results")
        # get the necessary stuff from the model
        self.model = model.model
        self.scenarios = scenarios
        self.system = model.system
        self.analysis = model.analysis
        self.solver = model.solver
        self.opt = model.opt
        self.energy_system = model.energy_system
        self.params = model.parameters
        self.vars = model.variables
        self.constraints = model.constraints

        # get name or directory
        self.model_name = model_name
        self.name_dir = pathlib.Path(self.analysis["folder_output"]).joinpath(self.model_name)

        # deal with the subfolder
        self.subfolder = subfolder
        # here we make use of the fact that None and "" both evaluate to False but any non-empty string doesn't
        if self.subfolder:
            self.name_dir = self.name_dir.joinpath(self.subfolder)
        # create the output directory
        os.makedirs(self.name_dir, exist_ok=True)

        # check if we should overwrite output
        self.overwrite = self.analysis["overwrite_output"]
        # get the compression param
        self.output_format = self.analysis["output_format"]

        # save the pyomo yml
        if self.analysis["write_results_yml"]:
            with RedirectStdStreams(open(os.path.join(self.name_dir, "results.yml"), "w+")):
                model.results.write()

        # save everything
        self.save_param()
        self.save_var()
        self.save_duals()
        self.save_system()
        self.save_analysis()
        self.save_scenarios()
        self.save_solver()
        if save_opt:
            self.save_opt()

        # extract and save sequence time steps, we transform the arrays to lists
        self.dict_sequence_time_steps = self.flatten_dict(self.energy_system.time_steps.get_sequence_time_steps_dict())
        self.save_sequence_time_steps(scenario=scenario_name)

        # case where we should run the post-process as normal
        if model.analysis['postprocess']:
            pass  # TODO: implement this...  # self.process()

    def write_file(self, name, dictionary, format=None):
        """
        Writes the dictionary to file as json, if compression attribute is True, the serialized json is compressed
        and saved as binary file
        :param name: Filename without extension
        :param dictionary: The dictionary to save
        :param format: Force the format to use, if None use output_format attribute of instance
        """

        # set the format
        if format is None:
            format = self.output_format
        if format == "gzip" or format == "json":
            # serialize to string
            serialized_dict = json.dumps(dictionary, indent=2)

            # if the string is larger than the max output size we compress anyway
            force_compression = False
            if format == "json" and sys.getsizeof(serialized_dict) / 1024 ** 2 > self.analysis["max_output_size_mb"]:
                print(f"WARNING: The file {name}.json would be larger than the maximum allowed output size of "
                      f"{self.analysis['max_output_size_mb']}MB, compressing...")
                force_compression = True

            # prep output file
            if format == "gzip" or force_compression:
                # compress
                f_name = f"{name}.gzip"
                f_mode = "wb"
                serialized_dict = zlib.compress(serialized_dict.encode())
            else:
                # write normal json
                f_name = f"{name}.json"
                f_mode = "w+"

            # write if necessary
            if self.overwrite or not os.path.exists(f_name):
                with open(f_name, f_mode) as outfile:
                    outfile.write(serialized_dict)

        elif format == "h5":
            f_name = f"{name}.h5"
            if self.overwrite or not os.path.exists(f_name):
                with h5py.File(f_name, "w") as outfile:
                    utils.dump(data=dictionary, hdf=outfile)


    def save_param(self):
        """ Saves the Param values to a json file which can then be
        post-processed immediately or loaded and postprocessed at some other time"""

        # dataframe serialization
        data_frames = {}
        for param in self.params.docs.keys():
            # get the values
            vals = getattr(self.params, param)
            doc = self.params.docs[param]
            index_list = self.get_index_list(doc)
            if len(index_list) == 0:
                index_names = None
            elif len(index_list) == 1:
                index_names = index_list[0]
            else:
                index_names = index_list
            # create a dictionary if necessary
            if not isinstance(vals, dict):
                indices = pd.Index(data=[0], name=index_names)
                data = [vals]
            # if the returned dict is emtpy we create a nan value
            elif len(vals) == 0:
                if len(index_list) > 1:
                    indices = pd.MultiIndex(levels=[[]] * len(index_names), codes=[[]] * len(index_names), names=index_names)
                else:
                    indices = pd.Index(data=[], name=index_names)
                data = []
            # we read out everything
            else:
                indices = list(vals.keys())
                data = list(vals.values())

                # create a multi index if necessary
                if len(indices) >= 1 and isinstance(indices[0], tuple):
                    if len(index_list) == len(indices[0]):
                        indices = pd.MultiIndex.from_tuples(indices, names=index_names)
                    else:
                        indices = pd.MultiIndex.from_tuples(indices)
                else:
                    if len(index_list) == 1:
                        indices = pd.Index(data=indices, name=index_names)
                    else:
                        indices = pd.Index(data=indices)

            # create dataframe
            df = pd.DataFrame(data=data, columns=["value"], index=indices)

            # update dict
            data_frames[param] = self._transform_df(df,doc)

        # write to json
        self.write_file(self.name_dir.joinpath('param_dict'), data_frames)

    def save_var(self):
        """ Saves the variable values to a json file which can then be
        post-processed immediately or loaded and postprocessed at some other time"""

        # dataframe serialization
        data_frames = {}
        for var in self.model.component_objects(pe.Var, active=True):
            if var.name in self.vars.docs:
                doc = self.vars.docs[var.name]
                index_list = self.get_index_list(doc)
                if len(index_list) == 0:
                    index_names = None
                elif len(index_list) == 1:
                    index_names = index_list[0]
                else:
                    index_names = index_list
            else:
                index_list = []
                doc = None
            # get indices and values
            indices = [index for index in var]
            values = [getattr(var[index], "value", None) for index in indices]

            # create a multi index if necessary
            if len(indices) >= 1 and isinstance(indices[0], tuple):
                if len(index_list) == len(indices[0]):
                    indices = pd.MultiIndex.from_tuples(indices, names=index_names)
                else:
                    indices = pd.MultiIndex.from_tuples(indices)
            else:
                if len(index_list) == 1:
                    indices = pd.Index(data=indices, name=index_names)
                else:
                    indices = pd.Index(data=indices)

            # create dataframe
            df = pd.DataFrame(data=values, columns=["value"], index=indices)

            # we transform the dataframe to a json string and load it into the dictionary as dict
            data_frames[var.name] = self._transform_df(df,doc)

        self.write_file(self.name_dir.joinpath('var_dict'), data_frames)
        
    def save_duals(self):
        """ Saves the dual variable values to a json file which can then be
        post-processed immediately or loaded and postprocessed at some other time"""
        if not self.solver["add_duals"]:
            return
        # dataframe serialization
        data_frames_duals = {}
        data_frames_expr = {}
        for constraint in self.model.component_objects(pe.Constraint, active=True):
            if constraint.name in self.constraints.docs:
                doc = self.constraints.docs[constraint.name]
                index_list = self.get_index_list(doc)
                if len(index_list) == 0:
                    index_names = None
                elif len(index_list) == 1:
                    index_names = index_list[0]
                else:
                    index_names = index_list
            else:
                index_list = []
                doc = None
            # get indices and values
            indices = [index for index in constraint]
            duals = [self.model.dual[constraint[index]] for index in indices if constraint[index] in self.model.dual]
            # expr = [constraint[index].expr.to_string() for index in indices]

            # create a multi index if necessary
            if len(indices) >= 1 and isinstance(indices[0], tuple):
                if len(index_list) == len(indices[0]):
                    indices = pd.MultiIndex.from_tuples(indices, names=index_names)
                else:
                    indices = pd.MultiIndex.from_tuples(indices)
            else:
                if len(index_list) == 1:
                    indices = pd.Index(data=indices, name=index_names)
                else:
                    indices = pd.Index(data=indices)

            # create dataframe
            df_dual = pd.DataFrame(data=duals, columns=["value"], index=indices)
            # df_expr = pd.DataFrame(data=expr, columns=["value"], index=indices)

            data_frames_duals[constraint.name] = self._transform_df(df_dual,doc)
            # data_frames_expr[constraint.name] = self._transform_df(df_expr,doc)

        # self.write_file(self.name_dir.joinpath('constraint_dict'), data_frames_expr)
        self.write_file(self.name_dir.joinpath('dual_dict'), data_frames_duals)


    def save_system(self):
        """
        Saves the system dict as json
        """

        # This we only need to save once
        if self.subfolder:
            fname = self.name_dir.parent.joinpath('system')
        else:
            fname = self.name_dir.joinpath('system')
        self.write_file(fname, self.system, format="json")

    def save_analysis(self):
        """
        Saves the analysis dict as json
        """

        # This we only need to save once
        if self.subfolder:
            fname = self.name_dir.parent.joinpath('analysis')
        else:
            fname = self.name_dir.joinpath('analysis')
        self.write_file(fname, self.analysis, format="json")

    def save_scenarios(self):
        """
        Saves the analysis dict as json
        """

        # This we only need to save once
        if self.subfolder:
            fname = self.name_dir.parent.joinpath('scenarios')
        else:
            fname = self.name_dir.joinpath('scenarios')
        self.write_file(fname, self.scenarios, format="json")

    def save_solver(self):
        """
        Saves the solver dict as json
        """

        # This we only need to save once
        if self.subfolder:
            fname = self.name_dir.parent.joinpath('solver')
        else:
            fname = self.name_dir.joinpath('solver')
        self.write_file(fname, self.solver, format="json")

    def save_opt(self):
        """
        Saves the opt dict as json
        """

        self.write_file(self.name_dir.joinpath('opt_dict'), self.opt.__dict__)

        # copy the log file
        shutil.copy2(os.path.abspath(self.opt._log_file), self.name_dir)

    def save_sequence_time_steps(self, scenario=None):
        """
        Saves the dict_all_sequence_time_steps dict as json
        """
        # add the scenario name
        if scenario is not None:
            add_on = f"_{scenario}"
        else:
            add_on = ""

            # This we only need to save once
        if self.subfolder:
            fname = self.name_dir.parent.joinpath(f'dict_all_sequence_time_steps{add_on}')
        else:
            fname = self.name_dir.joinpath(f'dict_all_sequence_time_steps{add_on}')

        self.write_file(fname, self.dict_sequence_time_steps)

    def _transform_df(self,df,doc):
        """
        we transform the dataframe to a json string and load it into the dictionary as dict
        """
        if self.output_format == "h5":
            # need an array wrap because null bytes cause errors
            compressed_df = np.array([zlib.compress(df.to_json(orient="table", indent=2).encode())])
            dataframe = {"dataframe": compressed_df, "docstring": doc}
        else:
            dataframe = {"dataframe": json.loads(df.to_json(orient="table", indent=2)),
                                            "docstring": doc}
        return dataframe

    def flatten_dict(self, dictionary):
        """
        Creates a copy of the dictionary where all numpy arrays are recursively flattened to lists such that it can
        be saved as json file
        :param dictionary: The input dictionary
        :return: A copy of the dictionary containing lists instead of arrays
        """
        # create a copy of the dict to avoid overwrite
        out_dict = dict()

        # falten all arrays
        for k, v in dictionary.items():
            # transform the key None to 'null'
            if k is None:
                k = 'null'

            # recursive call
            if isinstance(v, dict):
                out_dict[k] = self.flatten_dict(v)  # flatten the array to list
            elif isinstance(v, np.ndarray):
                # Note: list(v) creates a list of np objects v.tolist() not
                out_dict[k] = v.tolist()
            # take as is
            else:
                out_dict[k] = v

        return out_dict

    def get_index_list(self, doc):
        """ get index list from docstring """
        split_doc = doc.split(";")
        for string in split_doc:
            if "dims" in string:
                break
        string = string.replace("dims:", "")
        index_list = string.split(",")
        index_list_final = []
        for index in index_list:
            if index in self.analysis["header_data_inputs"].keys():
                index_list_final.append(self.analysis["header_data_inputs"][index])  # else:  #     pass  #     # index_list_final.append(index)
        return index_list_final
