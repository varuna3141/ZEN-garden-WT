"""
:Title:        ZEN-GARDEN
:Created:      January-2022
:Authors:      Jacob Mannhardt (jmannhardt@ethz.ch)
:Organization: Laboratory of Reliability and Risk Engineering, ETH Zurich

Functions to apply time series aggregation to time series
"""
import pandas as pd
import numpy as np
import logging
import tsam.timeseriesaggregation as tsam
from zen_garden.model.objects.energy_system import EnergySystem
from zen_garden.model.objects.element import Element
from zen_garden.model.objects.technology.technology import Technology
from zen_garden.model.objects.technology.storage_technology import StorageTechnology


class TimeSeriesAggregation(object):
    """
    Class containing methods to apply time series aggregation
    """
    def __init__(self, energy_system: EnergySystem):
        """ initializes the time series aggregation. The data is aggregated for a single year and then concatenated

        :param energy_system: The energy system to use"""
        logging.info("\n--- Time series aggregation ---")
        self.energy_system = energy_system
        self.optimization_setup = energy_system.optimization_setup
        self.system = self.optimization_setup.system
        self.analysis = self.optimization_setup.analysis
        self.header_set_time_steps = self.analysis['header_data_inputs']["set_time_steps"]
        # if set_time_steps as input (because already aggregated), use this as base time step, otherwise self.set_base_time_steps
        self.set_base_time_steps = self.energy_system.set_base_time_steps_yearly
        self.number_typical_periods = min(self.system["unaggregated_time_steps_per_year"], self.system["aggregated_time_steps_per_year"])
        self.conducted_tsa = False
        # if number of time steps >= number of base time steps, skip aggregation
        if self.number_typical_periods < np.size(self.set_base_time_steps) and self.system["conduct_time_series_aggregation"]:
            # select time series
            self.select_ts_of_all_elements()
            if not self.df_ts_raw.empty:
                # run time series aggregation to create typical periods
                self.run_tsa()
            # nothing to aggregate
            else:
                assert len(self.excluded_ts) == 0, "Do not exclude any time series from aggregation, if there is then nothing else to aggregate!"
                # aggregate to single time step
                self.single_ts_tsa()
        else:
            self.typical_periods = pd.DataFrame()
            set_time_steps = self.set_base_time_steps
            time_step_duration = self.energy_system.time_steps.calculatetime_step_duration(set_time_steps, self.set_base_time_steps)
            sequence_time_steps = np.concatenate([[time_step] * time_step_duration[time_step] for time_step in time_step_duration])
            self.set_time_attributes(self, set_time_steps, time_step_duration, sequence_time_steps)
            # set aggregated time series
            self.set_aggregated_ts_all_elements()
        # repeat order of operational time steps and link with investment and yearly time steps
        self.repeat_sequence_time_steps_for_all_years()
        logging.info("Calculate operational time steps for storage levels")
        for element in self.optimization_setup.get_all_elements(StorageTechnology):
            # calculate time steps of storage levels
            element.calculate_time_steps_storage_level(conducted_tsa=self.conducted_tsa)

    def select_ts_of_all_elements(self):
        """ this method retrieves the raw time series for the aggregation of all input data sets. """
        self.get_excluded_ts()
        dict_raw_ts = {}
        for element in self.optimization_setup.get_all_elements(Element):
            df_ts_raw = self.extract_raw_ts(element, self.header_set_time_steps)
            if not df_ts_raw.empty:
                dict_raw_ts[element.name] = df_ts_raw
        if dict_raw_ts:
            self.df_ts_raw = pd.concat(dict_raw_ts.values(), axis=1, keys=dict_raw_ts.keys())
        else:
            self.df_ts_raw = pd.Series()

    def substitute_column_names(self, direction="flatten"):
        """ this method substitutes the column names to have flat columns names (otherwise sklearn warning)

        :param direction: #TODO describe parameter/return
        """
        if direction == "flatten":
            if not hasattr(self, "column_names_original"):
                self.column_names_original = self.df_ts_raw.columns
                self.column_names_flat = [str(index) for index in self.column_names_original]
                self.df_ts_raw.columns = self.column_names_flat
        elif direction == "raise":
            self.typical_periods = self.typical_periods[self.column_names_flat]
            self.typical_periods.columns = self.column_names_original

    def run_tsa(self):
        """ this method runs the time series aggregation """
        # substitute column names
        self.substitute_column_names(direction="flatten")
        # create aggregation object
        self.aggregation = tsam.TimeSeriesAggregation(timeSeries=self.df_ts_raw, noTypicalPeriods=self.number_typical_periods,
            hoursPerPeriod=self.analysis["time_series_aggregation"]["hoursPerPeriod"], resolution=self.analysis["time_series_aggregation"]["resolution"],
            clusterMethod=self.analysis["time_series_aggregation"]["clusterMethod"], solver=self.analysis["time_series_aggregation"]["solver"],
            extremePeriodMethod=self.analysis["time_series_aggregation"]["extremePeriodMethod"], rescaleClusterPeriods=self.analysis["time_series_aggregation"]["rescaleClusterPeriods"],
            representationMethod=self.analysis["time_series_aggregation"]["representationMethod"])
        # create typical periods
        self.typical_periods = self.aggregation.createTypicalPeriods().reset_index(drop=True)
        self.set_time_attributes(self, self.aggregation.clusterPeriodIdx, self.aggregation.clusterPeriodNoOccur, self.aggregation.clusterOrder)
        # resubstitute column names
        self.substitute_column_names(direction="raise")
        # set aggregated time series
        self.set_aggregated_ts_all_elements()
        self.conducted_tsa = True

    def set_aggregated_ts_all_elements(self):
        """ this method sets the aggregated time series and sets the necessary attributes after the aggregation to a single time grid """
        for element in self.optimization_setup.get_all_elements(Element):
            raw_ts = getattr(element, "raw_time_series")
            # set_time_steps, duration and sequence time steps
            element.set_time_steps_operation = list(self.set_time_steps)
            element.time_steps_operation_duration = self.time_steps_duration
            element.sequence_time_steps = self.sequence_time_steps

            # iterate through raw time series
            for ts in raw_ts:
                index_names = list(raw_ts[ts].index.names)
                index_names.remove(self.header_set_time_steps)
                df_ts = raw_ts[ts].unstack(level=index_names)

                df_aggregated_ts = pd.DataFrame(index=self.set_time_steps, columns=df_ts.columns)
                # columns which are in aggregated time series and which are not
                if element.name in self.typical_periods and ts in self.typical_periods[element.name]:
                    df_typical_periods = self.typical_periods[element.name, ts]
                    aggregated_columns = df_ts.columns.intersection(df_typical_periods.columns)
                    not_aggregated_columns = df_ts.columns.difference(df_typical_periods.columns)
                    # aggregated columns
                    df_aggregated_ts[aggregated_columns] = self.typical_periods[element.name, ts][aggregated_columns]
                else:
                    not_aggregated_columns = df_ts.columns
                # not aggregated columns because excluded
                if (element.name, ts) in self.excluded_ts:
                    df_aggregated_ts = self.manually_aggregate_ts(df_ts)
                # not aggregated columns because constant
                else:
                    df_aggregated_ts[not_aggregated_columns] = df_ts.loc[df_aggregated_ts.index, not_aggregated_columns]
                # reorder
                df_aggregated_ts.index.names = [self.header_set_time_steps]
                df_aggregated_ts.columns.names = index_names
                df_aggregated_ts = df_aggregated_ts.stack(index_names)
                df_aggregated_ts.index = df_aggregated_ts.index.reorder_levels(index_names + [self.header_set_time_steps])
                setattr(element, ts, df_aggregated_ts)
                self.set_aggregation_indicators(element)

    def get_excluded_ts(self):
        """ gets the names of all elements and parameter ts that shall be excluded from the time series aggregation """
        self.excluded_ts = []
        if self.system["exclude_parameters_from_TSA"]:
            excluded_parameters = self.optimization_setup.energy_system.data_input.read_input_data("exclude_parameter_from_TSA")
            # exclude file exists
            if excluded_parameters is not None:
                for _,vals in excluded_parameters.iterrows():
                    element_name = vals[0]
                    parameter = vals[1]
                    element = self.optimization_setup.get_element(cls=Element, name=element_name)
                    # specific element
                    if element is not None:
                        if parameter is np.nan:
                            logging.warning(f"Excluding all parameters {', '.join(element.raw_time_series.keys())} of {element_name} from time series aggregation")
                            for parameter_name in element.raw_time_series:
                                self.excluded_ts.append((element_name,parameter_name))
                        elif parameter in element.raw_time_series:
                            self.excluded_ts.append((element_name,parameter))
                    # for an entire set of elements
                    else:
                        if parameter is np.nan:
                            logging.warning("Please specify a specific parameter to exclude from time series aggregation when not providing a specific element")
                        else:
                            element_class = self.optimization_setup.get_element_class(name=element_name)
                            if element_class is not None:
                                logging.info(f"Parameter {parameter} is excluded from time series aggregation for all elements in {element_name}")
                                class_elements = self.optimization_setup.get_all_elements(cls=element_class)
                                for class_element in class_elements:
                                    if parameter in class_element.raw_time_series:
                                        self.excluded_ts.append((class_element.name, parameter))
                            else:
                                logging.warning(f"Exclusion from time series aggregation: {element_name} is neither a specific element nor an element class.")
            # remove duplicates
            self.excluded_ts = [*set(self.excluded_ts)]
            self.excluded_ts.sort()

    def manually_aggregate_ts(self,df):
        """ manually aggregates time series for excluded parameters.

        :param df: dataframe that is manually aggregated
        :return agg_df: aggregated dataframe """
        agg_df = pd.DataFrame(index=self.set_time_steps,columns=df.columns)
        for time_step in self.set_time_steps:
            df_slice = df.loc[self.sequence_time_steps == time_step]
            if self.analysis["time_series_aggregation"]["clusterMethod"] == "k_means":
                agg_df.loc[time_step] = df_slice.mean(axis=0)
            elif self.analysis["time_series_aggregation"]["clusterMethod"] == "k_medoids":
                agg_df.loc[time_step] = df_slice.median(axis=0)
            else:
                raise NotImplementedError(f"Cluster method {self.analysis['time_series_aggregation']['clusterMethod']} not yet implemented for manually aggregating excluded time series")
        return agg_df.astype(float)

    def extract_raw_ts(self, element, header_set_time_steps):
        """ extract the time series from an element and concatenates the non-constant time series to a pd.DataFrame

        :param element: element of the optimization
        :param header_set_time_steps: name of set_time_steps
        :return df_ts_raw: pd.DataFrame with non-constant time series"""
        dict_raw_ts = {}
        raw_ts = getattr(element, "raw_time_series")
        for ts in raw_ts:
            raw_ts[ts].name = ts
            index_names = list(raw_ts[ts].index.names)
            index_names.remove(header_set_time_steps)
            df_ts = raw_ts[ts].unstack(level=index_names)
            # select time series that are not constant (rows have more than 1 unique entries)
            df_ts_non_constant = df_ts[df_ts.columns[df_ts.apply(lambda column: len(np.unique(column)) != 1)]]
            if (element.name,ts) in self.excluded_ts:
                df_empty = pd.DataFrame(index=df_ts_non_constant.index)
                dict_raw_ts[ts] = df_empty
            else:
                dict_raw_ts[ts] = df_ts_non_constant
        df_ts_raw = pd.concat(dict_raw_ts.values(), axis=1, keys=dict_raw_ts.keys())
        return df_ts_raw

    def link_time_steps(self, element):
        """ calculates the necessary overlapping time steps of the investment and operation of a technology for all years.
        It sets the union of the time steps for investment, operation and years.

        :param element: technology of the optimization """
        list_sequence_time_steps = [self.energy_system.time_steps.get_sequence_time_steps(element.name, "operation"),
                                    self.energy_system.time_steps.get_sequence_time_steps(None, "yearly")]

        unique_time_steps_sequences = self.unique_time_steps_multiple_indices(list_sequence_time_steps)
        if unique_time_steps_sequences:
            set_time_steps, time_steps_duration, sequence_time_steps = unique_time_steps_sequences
            # set sequence time steps
            self.energy_system.time_steps.set_sequence_time_steps(element.name, sequence_time_steps)
            # time series parameters
            self.overwrite_ts_with_expanded_timeindex(element, set_time_steps, sequence_time_steps)
            # set attributes
            self.set_time_attributes(element, set_time_steps, time_steps_duration, sequence_time_steps)
        else:
            # check to multiply the time series with the yearly variation
            self.yearly_variation_nonaggregated_ts(element)

    def convert_time_steps_operation2year(self, element):
        """ calculates the conversion of operational time steps to invest/yearly time steps

        :param element: element of the optimization
        """
        _sequence_time_steps_operation = getattr(element, "sequence_time_steps")
        _sequence_time_steps_yearly = getattr(self.energy_system, "sequence_time_steps_yearly")
        self.energy_system.time_steps.set_time_steps_operation2year_both_dir(element.name,_sequence_time_steps_operation,_sequence_time_steps_yearly)

    def overwrite_ts_with_expanded_timeindex(self, element, set_time_steps_operation, sequence_time_steps):
        """ this method expands the aggregated time series to match the extended operational time steps because of matching the investment and operational time sequences.

        :param element: element of the optimization
        :param set_time_steps_operation: new time steps operation
        :param sequence_time_steps: new order of operational time steps """
        header_set_time_steps = self.analysis['header_data_inputs']["set_time_steps"]
        old_sequence_time_steps = element.sequence_time_steps
        _idx_old2new = np.array([np.unique(old_sequence_time_steps[np.argwhere(idx == sequence_time_steps)]) for idx in set_time_steps_operation]).squeeze()
        for ts in element.raw_time_series:
            _old_ts = getattr(element, ts).unstack(header_set_time_steps)
            _new_ts = pd.DataFrame(index=_old_ts.index, columns=set_time_steps_operation)
            _new_ts = _old_ts.loc[:, _idx_old2new[_new_ts.columns]].T.reset_index(drop=True).T
            _new_ts.columns.names = [header_set_time_steps]
            _new_ts = _new_ts.stack()
            # multiply with yearly variation
            _new_ts = self.multiply_yearly_variation(element, ts, _new_ts)
            # overwrite time series
            setattr(element, ts, _new_ts)

    def yearly_variation_nonaggregated_ts(self, element):
        """ multiply the time series with the yearly variation if the element's time series are not aggregated

        :param element: element of the optimization """
        for ts in element.raw_time_series:
            # multiply with yearly variation
            _new_ts = self.multiply_yearly_variation(element, ts, getattr(element, ts))
            # overwrite time series
            setattr(element, ts, _new_ts)

    def multiply_yearly_variation(self, element, ts_name, ts):
        """ this method multiplies time series with the yearly variation of the time series
        The index of the variation is the same as the original time series, just time and year substituted

        :param element: technology of the optimization
        :param ts_name: name of time series
        :param ts: time series
        :return multipliedTimeSeries: ts multiplied with yearly variation """
        if hasattr(element.data_input, ts_name + "_yearly_variation"):
            _yearly_variation = getattr(element.data_input, ts_name + "_yearly_variation")
            header_set_time_steps = self.analysis['header_data_inputs']["set_time_steps"]
            header_set_time_steps_yearly = self.analysis['header_data_inputs']["set_time_steps_yearly"]
            _ts = ts.unstack(header_set_time_steps)
            _yearly_variation = _yearly_variation.unstack(header_set_time_steps_yearly)
            # if only one unique value
            if len(np.unique(_yearly_variation)) == 1:
                ts = _ts.stack() * np.unique(_yearly_variation)[0]
            else:
                for year in self.energy_system.set_time_steps_yearly:
                    if not all(_yearly_variation[year] == 1):
                        _base_time_steps = self.energy_system.time_steps.decode_time_step(None, year, "yearly")
                        _element_time_steps = self.energy_system.time_steps.encode_time_step(element.name, _base_time_steps, yearly=True)
                        _ts.loc[:, _element_time_steps] = _ts[_element_time_steps].multiply(_yearly_variation[year], axis=0).fillna(0)
                ts = _ts.stack()
        # round down if lower than decimal points
        _rounding_value = 10 ** (-self.optimization_setup.solver["rounding_decimal_points_ts"])
        ts[ts.abs() < _rounding_value] = 0
        return ts

    def repeat_sequence_time_steps_for_all_years(self):
        """ this method repeats the operational time series for all years."""
        logging.info("Repeat the time series sequences for all years")
        optimized_years = len(self.energy_system.set_time_steps_yearly)
        # concatenate the order of time steps for all elements and link with investment and yearly time steps
        for element in self.optimization_setup.get_all_elements(Element):
            # optimized_years = EnergySystem.get_system()["optimized_years"]
            old_sequence_time_steps = self.optimization_setup.get_attribute_of_specific_element(Element, element.name, "sequence_time_steps")
            new_sequence_time_steps = np.hstack([old_sequence_time_steps] * optimized_years)
            element.sequence_time_steps = new_sequence_time_steps
            self.energy_system.time_steps.set_sequence_time_steps(element.name, element.sequence_time_steps)
            # calculate the time steps in operation to link with investment and yearly time steps
            self.link_time_steps(element)
            # set operation2year and year2operation time step dict
            self.convert_time_steps_operation2year(element)

    def set_aggregation_indicators(self, element):
        """ this method sets the indicators that element is aggregated

        :param element: element of the optimization
        """
        # add order of time steps to Energy System
        self.energy_system.time_steps.set_sequence_time_steps(element.name, element.sequence_time_steps, time_step_type="operation")
        element.aggregated = True

    def unique_time_steps_multiple_indices(self, list_sequence_time_steps):
        """ this method returns the unique time steps of multiple time grids

        :param list_sequence_time_steps: #TODO describe parameter/return
        :return (set_time_steps, time_steps_duration, sequence_time_steps): time steps, duration and sequence
        """
        sequence_time_steps = np.zeros(np.size(list_sequence_time_steps, axis=1)).astype(int)
        combined_sequence_time_steps = np.vstack(list_sequence_time_steps)
        unique_combined_time_steps, unique_indices, count_combined_time_steps = np.unique(combined_sequence_time_steps, axis=1, return_counts=True, return_index=True)
        # if unique time steps are the same as original, or if the second until last only have a single unique value
        if len(np.unique(combined_sequence_time_steps[0, :])) == len(combined_sequence_time_steps[0, :]) or len(np.unique(combined_sequence_time_steps[1:, :], axis=1)[0]) == 1:
            return None
        set_time_steps = []
        time_steps_duration = {}
        for _idx_unique_time_steps, _count_unique_time_steps in enumerate(count_combined_time_steps):
            set_time_steps.append(_idx_unique_time_steps)
            time_steps_duration[_idx_unique_time_steps] = _count_unique_time_steps
            _unique_time_step = unique_combined_time_steps[:, _idx_unique_time_steps]
            _idx_input = np.argwhere(np.all(combined_sequence_time_steps.T == _unique_time_step, axis=1))
            # fill new order time steps 
            sequence_time_steps[_idx_input] = _idx_unique_time_steps
        return (set_time_steps, time_steps_duration, sequence_time_steps)

    def overwrite_raw_ts(self, element):
        """ this method overwrites the raw time series to the already once aggregated time series

        :param element: technology of the optimization """
        for ts in element.raw_time_series:
            element.raw_time_series[ts] = getattr(element, ts)

    def single_ts_tsa(self):
        """ manually aggregates the constant time series to single ts """
        if self.number_typical_periods > 1:
            logging.warning("You are trying to aggregate constant time series to more than one representative time step. This setting is overwritten to one representative time step.")
            self.number_typical_periods = 1
        unaggregated_time_steps = self.system["unaggregated_time_steps_per_year"]
        set_time_steps = [0]
        time_steps_duration = {0:unaggregated_time_steps}
        sequence_time_steps = np.hstack(set_time_steps*unaggregated_time_steps)
        self.set_time_attributes(self, set_time_steps, time_steps_duration, sequence_time_steps)
        # create empty typical_period df
        self.typical_periods = pd.DataFrame(index=set_time_steps)
        # set aggregated time series
        self.set_aggregated_ts_all_elements()
        self.conducted_tsa = True

    @staticmethod
    def set_time_attributes(element, set_time_steps, time_steps_duration, sequence_time_steps):
        """ this method sets the operational time attributes of an element.

        :param element: element of the optimization
        :param set_time_steps: set_time_steps of operation
        :param time_steps_duration: time_steps_duration of operation
        :param sequence_time_steps: sequence of operation """
        if isinstance(element, TimeSeriesAggregation):
            element.set_time_steps = set_time_steps
            element.time_steps_duration = time_steps_duration
            element.sequence_time_steps = sequence_time_steps
        else:
            element.set_time_steps_operation = set_time_steps
            element.time_steps_operation_duration = time_steps_duration
            element.sequence_time_steps = sequence_time_steps
