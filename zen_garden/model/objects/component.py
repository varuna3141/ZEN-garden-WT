"""===========================================================================================================================================================================
Title:          ZEN-GARDEN
Created:        July-2022
Authors:        Jacob Mannhardt (jmannhardt@ethz.ch)
Organization:   Laboratory of Reliability and Risk Engineering, ETH Zurich

Description:    Class containing parameters. This is a proxy for pyomo parameters, since the construction of parameters has a significant overhead.
==========================================================================================================================================================================="""
import copy
import itertools
import logging
from itertools import zip_longest

import linopy as lp
import numpy as np
import pandas as pd
import pyomo.environ as pe
import xarray as xr


class Component:

    def __init__(self):
        self.docs = {}

    @staticmethod
    def compile_doc_string(doc, index_list, name, domain=None):
        """ compile docstring from doc and index_list"""
        assert type(doc) == str, f"Docstring {doc} has wrong format. Must be 'str' but is '{type(doc).__name__}'"
        # check for prohibited strings
        prohibited_strings = [",", ";", ":", "/", "name", "doc", "dims", "domain"]
        original_doc = copy.copy(doc)
        for string in prohibited_strings:
            if string in doc:
                logging.warning(f"Docstring '{original_doc}' contains prohibited string '{string}'. Occurrences are dropped.")
                doc = doc.replace(string, "")
        # joined index names
        joined_index = ",".join(index_list)
        # complete doc string
        complete_doc = f"name:{name};doc:{doc};dims:{joined_index}"
        if domain:
            complete_doc += f";domain:{domain}"
        return complete_doc

    @staticmethod
    def get_index_names_data(index_list):
        """ splits index_list in data and index names """
        if isinstance(index_list, tuple):
            index_values, index_names = index_list
        elif isinstance(index_list, list):
            index_values = list(itertools.product(*index_list[0]))
            index_names = index_list[1]
        else:
            raise TypeError(f"Type {type(index_list)} unknown to extract index names.")
        return index_values, index_names


class IndexSet(Component):
    def __init__(self):
        """ initialization of the IndexSet object """
        # base class init
        super().__init__()

        # attributes
        self.sets = {}
        self.index_sets = {}

    def add_set(self, name, data, doc, index_set=None):
        """
        Adds a set to the IndexSets (this set it not indexed)
        :param data: The data used for the init
        :param doc: The docstring of the set
        :param index_set: The name of the index set if the set itself is indexed
        """

        if name in self.sets:
            logging.warning(f"{name} already added. Will be overwritten!")

        # added data and docs
        self.sets[name] = data
        self.docs[name] = doc
        if index_set is not None:
            self.index_sets[name] = index_set

    def is_indexed(self, name):
        """
        Checks if the set with the name is indexed
        :param name: The name of the set
        :return: True if indexed, False otherwise
        """

        return name in self.index_sets

    def get_index_name(self, name):
        """
        Returns the index name of an indexed set
        :param name: The name of the indexed set
        :return: The name of the index set
        """

        if not self.is_indexed(name=name):
            raise ValueError(f"Set {name} is not an indexed set!")
        return self.index_sets[name]

    @staticmethod
    def tuple_to_arr(index_values):
        """
        Transforms a list of tuples into a list of xarrays containing all elements from the corresponding tuple entry
        :param index_values: The list of tuples with the index values
        :return: A list of arrays
        """

        if isinstance(index_values[0], tuple):
            ndims = len(index_values[0])
            tmp_vals = [[] for _ in range(ndims)]
            for t in index_values:
                for i in range(ndims):
                    tmp_vals[i].append(t[i])
            index_arrs = [xr.DataArray(t) for t in tmp_vals]
        else:
            index_arrs = [xr.DataArray(index_values)]

        return index_arrs

    @staticmethod
    def indices_to_mask(index_values, index_list, bounds):
        """
        Transforms a list of index values into a mask
        :param index_values: A list of index values (tuples)
        :param index_list: The list of the names of the indices
        :param bounds: Either None, tuple, array or callable to define the bounds of the variable
        :return: The mask as xarray
        """

        # get the coords
        index_arrs = IndexSet.tuple_to_arr(index_values)
        coords = [np.unique(t.data) for t in index_arrs]

        # init the mask
        mask = xr.DataArray(False, coords=coords, dims=index_list)
        mask.loc[*index_arrs] = True

        # get the bounds
        lower = xr.DataArray(-np.inf, coords=coords, dims=index_list)
        upper = xr.DataArray(np.inf, coords=coords, dims=index_list)
        if isinstance(bounds, tuple):
            lower[...] = bounds[0]
            upper[...] = bounds[1]
        elif isinstance(bounds, np.ndarray):
            lower.loc[*index_arrs] = bounds[:,0]
            upper.loc[*index_arrs] = bounds[:,1]
        elif callable(bounds):
            tmp_low = []
            tmp_up = []
            for t in index_values:
                b = bounds(*t)
                tmp_low.append(b[0])
                tmp_up.append(b[1])
            lower.loc[*index_arrs] = tmp_low
            upper.loc[*index_arrs] = tmp_up
        elif bounds is None:
            lower = -np.inf
            upper = np.inf
        else:
            raise ValueError(f"bounds should be None, tuple, array or callable, is: {bounds}")

        return mask, lower, upper

    def as_tuple(self, name):
        """
        Returns the tuple, (set, [name]), e.g. for variable creation
        :param name: The name to retrieve
        :return: The tuple
        """

        return self.sets[name], [name]

    def __getitem__(self, name):
        """
        Returns a set
        :param name: The name of the set to get
        :return: The set that has the name
        """

        return self.sets[name]

    def __contains__(self, item):
        """
        The is for the "in" keyword
        :param item: The item to check
        :return: True if item is contained, False otherwies
        """

        return item in self.sets


class Parameter(Component):
    def __init__(self):
        """ initialization of the parameter object """
        super().__init__()
        self.min_parameter_value = {"name": None, "value": None}
        self.max_parameter_value = {"name": None, "value": None}
        self.data_set = xr.Dataset()

    def add_parameter(self, name, data, doc):
        """ initialization of a parameter
        :param name: name of parameter
        :param data: non default data of parameter and index_names
        :param doc: docstring of parameter """

        if name not in self.docs.keys():
            data, index_list = self.get_index_names_data(data)
            # save if highest or lowest value
            self.save_min_max(data, name)
            # convert to dict
            data = self.convert_to_xarr(data, index_list)
            # set parameter
            self.data_set[name] = data

            # save additional parameters
            self.docs[name] = self.compile_doc_string(doc, index_list, name)
        else:
            logging.warning(f"Parameter {name} already added. Can only be added once")

    def save_min_max(self, data, name):
        """ stores min and max parameter """
        if isinstance(data, dict) and data:
            data = pd.Series(data)
        if isinstance(data, pd.Series):
            _abs = data.abs()
            _abs = _abs[(_abs != 0) & (_abs != np.inf)]
            if not _abs.empty:
                _idxmax = name + "_" + "_".join(map(str, _abs.index[_abs.argmax()]))
                _valmax = _abs.max()
                _idxmin = name + "_" + "_".join(map(str, _abs.index[_abs.argmin()]))
                _valmin = _abs.min()
            else:
                return
        else:
            if not data or (abs(data) == 0) or (abs(data) == np.inf):
                return
            _abs = abs(data)
            _idxmax = name
            _valmax = _abs
            _idxmin = name
            _valmin = _abs
        if not self.max_parameter_value["name"]:
            self.max_parameter_value["name"] = _idxmax
            self.max_parameter_value["value"] = _valmax
            self.min_parameter_value["name"] = _idxmin
            self.min_parameter_value["value"] = _valmin
        else:
            if _valmax > self.max_parameter_value["value"]:
                self.max_parameter_value["name"] = _idxmax
                self.max_parameter_value["value"] = _valmax
            if _valmin < self.min_parameter_value["value"]:
                self.min_parameter_value["name"] = _idxmin
                self.min_parameter_value["value"] = _valmin

    @staticmethod
    def convert_to_xarr(data, index_list):
        """ converts the data to a dict if pd.Series"""
        if isinstance(data, pd.Series):
            # if single entry in index
            if len(data.index[0]) == 1:
                data.index = pd.Index(sum(data.index.values, ()))
            if len(data.index.names) == len(index_list):
                data.index.names = index_list
            data = data.to_xarray()
        return data

    def as_xarray(self, pname, indices):
        """
        Returns a xarray of the param
        :param pname: The name of the param
        :param indices: A list of indices to extract
        :return: An xarray of the param
        """

        p = getattr(self, pname)
        if isinstance(indices[0], tuple):
            return xr.DataArray([p[*args] for args in indices])
        else:
            return xr.DataArray([p[i] for i in indices])

    def __getitem__(self, item):
        """
        The get item method to directly access the underlying dataset
        :param item: The item to retireve
        :return: The xarray paramter
        """

        return self.data_set[item]


class Variable(Component):
    def __init__(self):
        super().__init__()

    def add_variable(self, model: lp.Model, name, index_sets, integer=False, binary=False, bounds=None, doc=""):
        """ initialization of a variable
        :param model: parent block component of variable, must be linopy model
        :param name: name of variable
        :param index_sets: Tuple of index values and index names
        :param integer: If it is an integer variable
        :param binary: If it is a binary variable
        :param bounds:  bounds of variable
        :param doc: docstring of variable """

        if name not in self.docs.keys():
            index_values, index_list = self.get_index_names_data(index_sets)
            mask, lower, upper = IndexSet.indices_to_mask(index_values, index_list, bounds)
            model.add_variables(lower=lower, upper=upper, integer=integer, binary=binary, name=name, mask=mask, coords=mask.coords)

            # save variable doc
            if integer:
                domain = "Integers"
            elif binary:
                domain = "Binary"
            else:
                if isinstance(bounds, tuple) and bounds[0] == 0:
                    domain = "NonNegativeReals"
                elif callable(bounds) or isinstance(bounds, np.ndarray):
                    domain = "BoundedReals"
                else:
                    domain = "Reals"
            self.docs[name] = self.compile_doc_string(doc, index_list, name, domain)
        else:
            logging.warning(f"Variable {name} already added. Can only be added once")


class Constraint(Component):
    def __init__(self):
        super().__init__()

    def add_constraint_block(self, model: lp.Model, name, constraint, doc=""):
        """ initialization of a constraint
        :param model: The linopy model
        :param name: name of variable
        :param constraint: The constraint to add
        :param doc: docstring of variable
        """

        if name not in self.docs.keys():
            model.add_constraints(constraint, name=name)
            # save constraint doc
            index_list = list(constraint.coords.dims)
            self.docs[name] = self.compile_doc_string(doc, index_list, name)
        else:
            logging.warning(f"{name} already added. Can only be added once")

    def add_constraint_rule(self, model: lp.Model, name, index_sets, rule, doc="", constraint_class=pe.Constraint):
        """ initialization of a variable
        :param model: The linopy model
        :param name: name of variable
        :param index_sets: indices and sets by which the variable is indexed
        :param rule: constraint rule
        :param doc: docstring of variable
        :param constraint_class: either pe.Constraint, pgdp.Disjunct,pgdp.Disjunction"""

        if name not in self.docs.keys():
            index_values, index_list = self.get_index_names_data(index_sets)

            # create the mask
            index_arrs = IndexSet.tuple_to_arr(index_values)
            coords = [np.unique(t.data) for t in index_arrs]
            coords = xr.DataArray(coords=coords, dims=index_list).coords
            shape = tuple(map(len, coords.values()))

            # if we only have a single index, there is no need to unpack
            if len(index_list) == 1:
                cons = [rule(arg) for arg in index_values]
            else:
                cons = [rule(*arg) for arg in index_values]

            # low level magic
            exprs = [con.lhs for con in cons]
            coeffs = np.array(tuple(zip_longest(*(e.coeffs for e in exprs), fillvalue=np.nan)))
            vars = np.array(tuple(zip_longest(*(e.vars for e in exprs), fillvalue=-1)))

            nterm = vars.shape[0]
            coeffs = coeffs.reshape((nterm, -1))
            vars = vars.reshape((nterm, -1))

            xr_coeffs = xr.DataArray(np.full(shape=(nterm, ) + shape, fill_value=np.nan), coords, dims=("_term", *coords))
            xr_coeffs.loc[:,*index_arrs] = coeffs
            xr_vars = xr.DataArray(np.full(shape=(nterm, ) + shape, fill_value=-1), coords, dims=("_term", *coords))
            xr_vars.loc[:, *index_arrs] = vars
            xr_ds = xr.Dataset({"coeffs": xr_coeffs, "vars": xr_vars}).transpose(..., "_term")
            xr_lhs = lp.LinearExpression(xr_ds, model)
            xr_sign = xr.DataArray("==", coords, dims=index_list)
            xr_sign.loc[*index_arrs] = [c.sign for c in cons]
            xr_rhs = xr.DataArray(0, coords, dims=index_list)
            xr_rhs.loc[*index_arrs] = [c.rhs for c in cons]
            model.add_constraints(xr_lhs, xr_sign, xr_rhs, name=name)

            constraint_class(index_values, rule=rule, doc=doc)
            # save constraint doc
            self.docs[name] = self.compile_doc_string(doc, index_list, name)
        else:
            logging.warning(f"{constraint_class.name} {name} already added. Can only be added once")
