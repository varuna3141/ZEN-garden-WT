"""===========================================================================================================================================================================
Title:          ZEN-GARDEN
Created:        October-2021
Authors:        Alissa Ganter (aganter@ethz.ch)
                Jacob Mannhardt (jmannhardt@ethz.ch)
Organization:   Laboratory of Reliability and Risk Engineering, ETH Zurich

Description:    Class defining the parameters, variables and constraints that hold for all technologies.
                The class takes the abstract optimization model as an input, and returns the parameters, variables and
                constraints that hold for all technologies.
==========================================================================================================================================================================="""
import logging

import pandas as pd
import pyomo.environ as pe
import pyomo.gdp as pgdp
import numpy as np
import linopy as lp
from ..element import Element


class Technology(Element):
    # set label
    label = "set_technologies"
    location_type = None

    def __init__(self, technology: str, optimization_setup):
        """init generic technology object
        :param technology: technology that is added to the model
        :param optimization_setup: The OptimizationSetup the element is part of """

        super().__init__(technology, optimization_setup)


    def store_input_data(self):
        """ retrieves and stores input data for element as attributes. Each Child class overwrites method to store different attributes """
        # set attributes of technology
        _set_location = self.location_type

        set_base_time_steps_yearly = self.energy_system.set_base_time_steps_yearly
        set_time_steps_yearly = self.energy_system.set_time_steps_yearly
        self.reference_carrier = [self.data_input.extract_attribute("reference_carrier", skip_warning=True)]
        self.energy_system.set_technology_of_carrier(self.name, self.reference_carrier)
        self.min_built_capacity = self.data_input.extract_attribute("min_built_capacity")["value"]
        self.max_built_capacity = self.data_input.extract_attribute("max_built_capacity")["value"]
        self.lifetime = self.data_input.extract_attribute("lifetime")["value"]
        self.construction_time = self.data_input.extract_attribute("construction_time")["value"]
        # maximum diffusion rate
        self.max_diffusion_rate = self.data_input.extract_input_data("max_diffusion_rate", index_sets=["set_time_steps_yearly"], time_steps=set_time_steps_yearly)

        # add all raw time series to dict
        self.raw_time_series = {}
        self.raw_time_series["min_load"] = self.data_input.extract_input_data("min_load", index_sets=[_set_location, "set_time_steps"], time_steps=set_base_time_steps_yearly)
        self.raw_time_series["max_load"] = self.data_input.extract_input_data("max_load", index_sets=[_set_location, "set_time_steps"], time_steps=set_base_time_steps_yearly)
        self.raw_time_series["opex_specific"] = self.data_input.extract_input_data("opex_specific", index_sets=[_set_location, "set_time_steps"], time_steps=set_base_time_steps_yearly)
        # non-time series input data
        self.fixed_opex_specific = self.data_input.extract_input_data("fixed_opex_specific", index_sets=[_set_location, "set_time_steps_yearly"], time_steps=set_time_steps_yearly)
        self.capacity_limit = self.data_input.extract_input_data("capacity_limit", index_sets=[_set_location])
        self.carbon_intensity_technology = self.data_input.extract_input_data("carbon_intensity", index_sets=[_set_location])
        # extract existing capacity
        self.set_existing_technologies = self.data_input.extract_set_existing_technologies()
        self.existing_capacity = self.data_input.extract_input_data("existing_capacity", index_sets=[_set_location, "set_existing_technologies"])
        self.existing_invested_capacity = self.data_input.extract_input_data("existing_invested_capacity", index_sets=[_set_location, "set_time_steps_yearly"], time_steps=set_time_steps_yearly)
        self.lifetime_existing_technology = self.data_input.extract_lifetime_existing_technology("existing_capacity", index_sets=[_set_location, "set_existing_technologies"])

    def calculate_capex_of_existing_capacities(self, storage_energy=False):
        """ this method calculates the annualized capex of the existing capacities """
        if self.__class__.__name__ == "StorageTechnology":
            if storage_energy:
                existing_capacities = self.existing_capacity_energy
            else:
                existing_capacities = self.existing_capacity
            existing_capex = existing_capacities.to_frame().apply(
                lambda _existing_capacity: self.calculate_capex_of_single_capacity(_existing_capacity.squeeze(), _existing_capacity.name, storage_energy), axis=1)
        else:
            existing_capacities = self.existing_capacity
            existing_capex = existing_capacities.to_frame().apply(lambda _existing_capacity: self.calculate_capex_of_single_capacity(_existing_capacity.squeeze(), _existing_capacity.name), axis=1)
        return existing_capex

    def calculate_capex_of_single_capacity(self, *args):
        """ this method calculates the annualized capex of the existing capacities. Is implemented in child class """
        raise NotImplementedError

    def calculate_fractional_annuity(self):
        """calculate fraction of annuity to depreciate investment"""
        _lifetime = self.lifetime
        _annuity = 1 / _lifetime
        # only account for fraction of year
        _fraction_year = self.optimization_setup.system["unaggregated_time_steps_per_year"] / self.optimization_setup.system["total_hours_per_year"]
        _fractional_annuity = _annuity * _fraction_year
        return _fractional_annuity

    def overwrite_time_steps(self, base_time_steps: int):
        """ overwrites set_time_steps_operation """
        set_time_steps_operation = self.energy_system.time_steps.encode_time_step(self.name, base_time_steps=base_time_steps, time_step_type="operation", yearly=True)

        # copy invest time steps
        self.set_time_steps_operation = set_time_steps_operation.squeeze().tolist()

    def add_newly_built_capacity_tech(self, built_capacity: pd.Series, capex: pd.Series, base_time_steps: int):
        """ adds the newly built capacity to the existing capacity
        :param built_capacity: pd.Series of newly built capacity of technology
        :param capex: pd.Series of capex of newly built capacity of technology
        :param base_time_steps: base time steps of current horizon step """
        system = self.optimization_setup.system
        # reduce lifetime of existing capacities and add new remaining lifetime
        self.lifetime_existing_technology = (self.lifetime_existing_technology - system["interval_between_years"]).clip(lower=0)
        # new capacity
        _time_step_years = self.energy_system.time_steps.encode_time_step(self.name, base_time_steps, "yearly", yearly=True)
        _newly_built_capacity = built_capacity[_time_step_years].sum(axis=1)
        _capex = capex[_time_step_years].sum(axis=1)
        # if at least one value unequal to zero
        if not (_newly_built_capacity == 0).all():
            # add new index to set_existing_technologies
            index_new_technology = max(self.set_existing_technologies) + 1
            self.set_existing_technologies = np.append(self.set_existing_technologies, index_new_technology)
            # add new remaining lifetime
            _lifetime_technology = self.lifetime_existing_technology.unstack()
            _lifetime_technology[index_new_technology] = self.lifetime
            self.lifetime_existing_technology = _lifetime_technology.stack()

            for type_capacity in list(set(_newly_built_capacity.index.get_level_values(0))):
                # if power
                if type_capacity == system["set_capacity_types"][0]:
                    _energy_string = ""
                # if energy
                else:
                    _energy_string = "_energy"
                _existing_capacity = getattr(self, "existing_capacity" + _energy_string)
                _capex_existing_capacity = getattr(self, "capex_existing_capacity" + _energy_string)
                # add new existing capacity
                _existing_capacity = _existing_capacity.unstack()
                _existing_capacity[index_new_technology] = _newly_built_capacity.loc[type_capacity]
                setattr(self, "existing_capacity" + _energy_string, _existing_capacity.stack())
                # calculate capex of existing capacity
                _capex_existing_capacity = _capex_existing_capacity.unstack()
                _capex_existing_capacity[index_new_technology] = _capex.loc[type_capacity]
                setattr(self, "capex_existing_capacity" + _energy_string, _capex_existing_capacity.stack())

    def add_newly_invested_capacity_tech(self, invested_capacity: pd.Series, step_horizon):
        """ adds the newly invested capacity to the list of invested capacity
        :param invested_capacity: pd.Series of newly built capacity of technology
        :param step_horizon: optimization time step """
        system = self.optimization_setup.system
        _newly_invested_capacity = invested_capacity[step_horizon]
        _newly_invested_capacity = _newly_invested_capacity.fillna(0)
        if not (_newly_invested_capacity == 0).all():
            for type_capacity in list(set(_newly_invested_capacity.index.get_level_values(0))):
                # if power
                if type_capacity == system["set_capacity_types"][0]:
                    _energy_string = ""
                # if energy
                else:
                    _energy_string = "_energy"
                _existing_invested_capacity = getattr(self, "existing_invested_capacity" + _energy_string)
                # add new existing invested capacity
                _existing_invested_capacity = _existing_invested_capacity.unstack()
                _existing_invested_capacity[step_horizon] = _newly_invested_capacity.loc[type_capacity]
                setattr(self, "existing_invested_capacity" + _energy_string, _existing_invested_capacity.stack())

    ### --- classmethods
    @classmethod
    def get_lifetime_range(cls, optimization_setup, tech, time, time_step_type: str = None):
        """ returns lifetime range of technology. If time_step_type, then converts the yearly time step 'time' to time_step_type """
        if time_step_type:
            time_step_year = optimization_setup.energy_system.time_steps.convert_time_step_operation2year(tech,time)
        else:
            time_step_year = time
        t_start, t_end = cls.get_start_end_time_of_period(optimization_setup, tech, time_step_year)

        return range(t_start, t_end + 1)

    @classmethod
    def get_available_existing_quantity(cls, optimization_setup, tech, capacity_type, loc, time, type_existing_quantity, time_step_type: str = None):
        """ returns existing quantity of 'tech', that is still available at invest time step 'time'.
        Either capacity or capex.
        :param optimization_setup: The OptimizationSetup the element is part of
        :param tech: name of technology
        :param capacity_type: type of capacity
        :param loc: location (node or edge) of existing capacity
        :param time: current time
        :param type_existing_quantity: capex or capacity
        :param time_step_type: type of time steps
        :return existing_quantity: existing capacity or capex of existing capacity
        """
        params = optimization_setup.parameters
        system = optimization_setup.system
        discount_rate = optimization_setup.analysis["discount_rate"]
        if time_step_type:
            time_step_year = optimization_setup.energy_system.time_steps.convert_time_step_operation2year(tech,time)
        else:
            time_step_year = time

        sets = optimization_setup.sets
        existing_quantity = 0
        if type_existing_quantity == "capacity":
            existing_variable = params.existing_capacity
        elif type_existing_quantity == "capex":
            existing_variable = params.capex_existing_capacity
        else:
            raise KeyError(f"Wrong type of existing quantity {type_existing_quantity}")

        for id_existing_capacity in sets["set_existing_technologies"][tech]:
            t_start = cls.get_start_end_time_of_period(optimization_setup, tech, time_step_year, id_existing_capacity=id_existing_capacity, loc=loc)
            # discount existing capex
            if type_existing_quantity == "capex":
                year_construction = max(0, time * system["interval_between_years"] - params.lifetime_technology.loc[tech].item() + params.lifetime_existing_technology.loc[tech, loc, id_existing_capacity].item())
                discount_factor = (1 + discount_rate) ** (time * system["interval_between_years"] - year_construction)
            else:
                discount_factor = 1
            # if still available at first base time step, add to list
            if t_start == sets["set_base_time_steps"][0] or t_start == time_step_year:
                existing_quantity += existing_variable.loc[tech, capacity_type, loc, id_existing_capacity].item() * discount_factor
        return existing_quantity

    @classmethod
    def get_start_end_time_of_period(cls, optimization_setup, tech, time_step_year, period_type="lifetime", clip_to_first_time_step=True, id_existing_capacity=None, loc=None):
        """ counts back the period (either lifetime of construction_time) back to get the start invest time step and returns start_time_step_year
        :param energy_system: The Energy system to add everything
        :param tech: name of technology
        :param time_step_year: current investment time step
        :param period_type: "lifetime" if lifetime is counted backwards, "construction_time" if construction time is counted backwards
        :param clip_to_first_time_step: boolean to clip the time step to first time step if time step too far in the past
        :param id_existing_capacity: id of existing capacity
        :param loc: location (node or edge) of existing capacity
        :return beganInPast: boolean if the period began before the first optimization step
        :return start_time_step_year,end_time_step_year: start and end of period in invest time step domain"""

        # get model and system
        energy_system = optimization_setup.energy_system
        params = optimization_setup.parameters
        sets = optimization_setup.sets
        system = optimization_setup.system
        # get which period to count backwards
        if period_type == "lifetime":
            period_time = params.lifetime_technology
        elif period_type == "construction_time":
            period_time = params.construction_time_technology
        else:
            raise NotImplemented(f"get_start_end_time_of_period not yet implemented for {period_type}")
        # get end_time_step_year
        if not isinstance(time_step_year, np.ndarray):
            end_time_step_year = time_step_year
        elif len(time_step_year) == 1:
            end_time_step_year = time_step_year[0]
        # if more than one investment time step
        else:
            end_time_step_year = time_step_year[-1]
            time_step_year = time_step_year[0]
        # convert period to interval of base time steps
        if id_existing_capacity is None:
            period_yearly = period_time.loc[tech].item()
        else:
            delta_lifetime = params.lifetime_existing_technology.loc[tech, loc, id_existing_capacity].item() - period_time.loc[tech].item()
            if delta_lifetime >= 0:
                if delta_lifetime <= (time_step_year - sets["set_time_steps_yearly"][0]) * system["interval_between_years"]:
                    return time_step_year
                else:
                    return -1
            period_yearly = params.lifetime_existing_technology.loc[tech, loc, id_existing_capacity].item()
        base_period = period_yearly / system["interval_between_years"] * system["unaggregated_time_steps_per_year"]
        base_period = round(base_period, optimization_setup.solver["rounding_decimal_points"])
        if int(base_period) != base_period:
            logging.warning(f"The period {period_type} of {tech} does not translate to an integer time interval in the base time domain ({base_period})")
        # decode to base time steps
        base_time_steps = energy_system.time_steps.decode_time_step(tech, time_step_year, time_step_type="yearly")
        if len(base_time_steps) == 0:
            return sets["set_base_time_steps"][0], sets["set_base_time_steps"][0] - 1
        base_time_step = base_time_steps[0]

        # if start_base_time_step is further in the past than first base time step, use first base time step
        if clip_to_first_time_step:
            start_base_time_step = int(max(sets["set_base_time_steps"][0], base_time_step - base_period + 1))
        else:
            start_base_time_step = int(base_time_step - base_period + 1)
        start_base_time_step = min(start_base_time_step, sets["set_base_time_steps"][-1])
        # if period of existing capacity, then only return the start base time step
        if id_existing_capacity is not None:
            return start_base_time_step
        start_time_step_year = energy_system.time_steps.encode_time_step(tech, start_base_time_step, time_step_type="yearly", yearly=True)[0]

        return start_time_step_year, end_time_step_year

    ### --- classmethods to construct sets, parameters, variables, and constraints, that correspond to Technology --- ###
    @classmethod
    def construct_sets(cls, optimization_setup):
        """ constructs the pe.Sets of the class <Technology>
        :param optimization_setup: The OptimizationSetup the element is part of """
        # construct the pe.Sets of the class <Technology>
        energy_system = optimization_setup.energy_system
        model = optimization_setup.model

        # conversion technologies
        optimization_setup.sets.add_set(name="set_conversion_technologies", data=energy_system.set_conversion_technologies,
                                        doc="Set of conversion technologies. Subset: set_technologies")
        # transport technologies
        optimization_setup.sets.add_set(name="set_transport_technologies", data=energy_system.set_transport_technologies,
                                        doc="Set of transport technologies. Subset: set_technologies")
        # storage technologies
        optimization_setup.sets.add_set(name="set_storage_technologies", data=energy_system.set_storage_technologies,
                                        doc="Set of storage technologies. Subset: set_technologies")
        # existing installed technologies
        optimization_setup.sets.add_set(name="set_existing_technologies", data=optimization_setup.get_attribute_of_all_elements(cls, "set_existing_technologies"),
                                        doc="Set of existing technologies. Subset: set_technologies",
                                        index_set="set_technologies")
        # reference carriers
        optimization_setup.sets.add_set(name="set_reference_carriers", data=optimization_setup.get_attribute_of_all_elements(cls, "reference_carrier"),
                                        doc="set of all reference carriers correspondent to a technology. Dimensions: set_technologies",
                                        index_set="set_technologies")
        # add pe.Sets of the child classes
        for subclass in cls.__subclasses__():
            subclass.construct_sets(optimization_setup)

    @classmethod
    def construct_params(cls, optimization_setup):
        """ constructs the pe.Params of the class <Technology>
        :param optimization_setup: The OptimizationSetup the element is part of """
        # construct pe.Param of the class <Technology>

        # existing capacity
        optimization_setup.parameters.add_parameter(name="existing_capacity",
            data=optimization_setup.initialize_component(cls, "existing_capacity", index_names=["set_technologies", "set_capacity_types", "set_location", "set_existing_technologies"], capacity_types=True),
            doc='Parameter which specifies the existing technology size')
        # existing capacity
        optimization_setup.parameters.add_parameter(name="existing_invested_capacity",
            data=optimization_setup.initialize_component(cls, "existing_invested_capacity", index_names=["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly_entire_horizon"],
                                                   capacity_types=True), doc='Parameter which specifies the size of the previously invested capacities')
        # minimum capacity
        optimization_setup.parameters.add_parameter(name="min_built_capacity",
            data=optimization_setup.initialize_component(cls, "min_built_capacity", index_names=["set_technologies", "set_capacity_types"], capacity_types=True),
            doc='Parameter which specifies the minimum technology size that can be installed')
        # maximum capacity
        optimization_setup.parameters.add_parameter(name="max_built_capacity",
            data=optimization_setup.initialize_component(cls, "max_built_capacity", index_names=["set_technologies", "set_capacity_types"], capacity_types=True),
            doc='Parameter which specifies the maximum technology size that can be installed')
        # lifetime existing technologies
        optimization_setup.parameters.add_parameter(name="lifetime_existing_technology",
            data=optimization_setup.initialize_component(cls, "lifetime_existing_technology", index_names=["set_technologies", "set_location", "set_existing_technologies"]),
            doc='Parameter which specifies the remaining lifetime of an existing technology')
        # lifetime existing technologies
        optimization_setup.parameters.add_parameter(name="capex_existing_capacity",
            data=optimization_setup.initialize_component(cls, "capex_existing_capacity", index_names=["set_technologies", "set_capacity_types", "set_location", "set_existing_technologies"],
                                                   capacity_types=True), doc='Parameter which specifies the annualized capex of an existing technology which still has to be paid')
        # lifetime newly built technologies
        optimization_setup.parameters.add_parameter(name="lifetime_technology", data=optimization_setup.initialize_component(cls, "lifetime", index_names=["set_technologies"]),
            doc='Parameter which specifies the lifetime of a newly built technology')
        # construction_time newly built technologies
        optimization_setup.parameters.add_parameter(name="construction_time_technology", data=optimization_setup.initialize_component(cls, "construction_time", index_names=["set_technologies"]),
            doc='Parameter which specifies the construction time of a newly built technology')
        # maximum diffusion rate, i.e., increase in capacity
        optimization_setup.parameters.add_parameter(name="max_diffusion_rate", data=optimization_setup.initialize_component(cls, "max_diffusion_rate", index_names=["set_technologies", "set_time_steps_yearly"]),
            doc="Parameter which specifies the maximum diffusion rate which is the maximum increase in capacity between investment steps")
        # capacity_limit of technologies
        optimization_setup.parameters.add_parameter(name="capacity_limit_technology",
            data=optimization_setup.initialize_component(cls, "capacity_limit", index_names=["set_technologies", "set_capacity_types", "set_location"], capacity_types=True),
            doc='Parameter which specifies the capacity limit of technologies')
        # minimum load relative to capacity
        optimization_setup.parameters.add_parameter(name="min_load",
            data=optimization_setup.initialize_component(cls, "min_load", index_names=["set_technologies", "set_capacity_types", "set_location", "set_time_steps_operation"], capacity_types=True),
            doc='Parameter which specifies the minimum load of technology relative to installed capacity')
        # maximum load relative to capacity
        optimization_setup.parameters.add_parameter(name="max_load",
            data=optimization_setup.initialize_component(cls, "max_load", index_names=["set_technologies", "set_capacity_types", "set_location", "set_time_steps_operation"], capacity_types=True),
            doc='Parameter which specifies the maximum load of technology relative to installed capacity')
        # specific opex
        optimization_setup.parameters.add_parameter(name="opex_specific", data=optimization_setup.initialize_component(cls, "opex_specific", index_names=["set_technologies", "set_location", "set_time_steps_operation"]),
            doc='Parameter which specifies the specific opex')
        # carbon intensity
        optimization_setup.parameters.add_parameter(name="carbon_intensity_technology", data=optimization_setup.initialize_component(cls, "carbon_intensity_technology", index_names=["set_technologies", "set_location"]),
            doc='Parameter which specifies the carbon intensity of each technology')
        # add pe.Param of the child classes
        for subclass in cls.__subclasses__():
            subclass.construct_params(optimization_setup)

    @classmethod
    def construct_vars(cls, optimization_setup):
        """ constructs the pe.Vars of the class <Technology>
        :param optimization_setup: The OptimizationSetup the element is part of """

        model = optimization_setup.model
        variables = optimization_setup.variables
        sets = optimization_setup.sets

        def capacity_bounds(tech, capacity_type, loc, time):
            """ return bounds of capacity for bigM expression
            :param tech: tech index
            :param capacity_type: either power or energy
            :param loc: location of capacity
            :param time: investment time step
            :return bounds: bounds of capacity"""
            # bounds only needed for Big-M formulation, thus if any technology is modeled with on-off behavior
            if tech in techs_on_off:
                system = optimization_setup.system
                params = optimization_setup.parameters
                if capacity_type == system["set_capacity_types"][0]:
                    _energy_string = ""
                else:
                    _energy_string = "_energy"
                _existing_capacity = getattr(params, "existing_capacity" + _energy_string)
                _max_built_capacity = getattr(params, "max_built_capacity" + _energy_string)
                _capacity_limit_technology = getattr(params, "capacity_limit_technology" + _energy_string)
                existing_capacities = 0
                for id_existing_technology in sets["set_existing_technologies"][tech]:
                    if params.lifetime_existing_technology.loc[tech, loc, id_existing_technology] > params.lifetime_technology.loc[tech]:
                        if time > params.lifetime_existing_technology.loc[tech, loc, id_existing_technology] - params.lifetime_technology.loc[tech]:
                            existing_capacities += _existing_capacity.loc[tech, capacity_type, loc, id_existing_technology]
                    elif time <= params.lifetime_existing_technology.loc[tech, loc, id_existing_technology] + 1:
                        existing_capacities += _existing_capacity.loc[tech, capacity_type, loc, id_existing_technology]

                max_built_capacity = len(sets["set_time_steps_yearly"]) * _max_built_capacity.loc[tech, capacity_type]
                max_capacity_limit_technology = _capacity_limit_technology.loc[tech, capacity_type, loc]
                bound_capacity = min(max_built_capacity + existing_capacities, max_capacity_limit_technology + existing_capacities)
                return 0, bound_capacity.item()
            else:
                return 0, np.inf

        # bounds only needed for Big-M formulation, thus if any technology is modeled with on-off behavior
        techs_on_off = cls.create_custom_set(["set_technologies", "set_on_off"], optimization_setup)[0]
        # construct pe.Vars of the class <Technology>
        # install technology
        variables.add_variable(model, name="install_technology", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup), binary=True,
            doc='installment of a technology at location l and time t')
        # capacity technology
        variables.add_variable(model, name="capacity", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
            bounds=capacity_bounds, doc='size of installed technology at location l and time t')
        # built_capacity technology
        variables.add_variable(model, name="built_capacity", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
            bounds=(0,np.inf), doc='size of built technology (invested capacity after construction) at location l and time t')
        # invested_capacity technology
        variables.add_variable(model, name="invested_capacity", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
            bounds=(0,np.inf), doc='size of invested technology at location l and time t')
        # capex of building capacity
        variables.add_variable(model, name="capex", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
            bounds=(0,np.inf), doc='capex for building technology at location l and time t')
        # annual capex of having capacity
        variables.add_variable(model, name="capex_yearly", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
            bounds=(0,np.inf), doc='annual capex for having technology at location l')
        # total capex
        variables.add_variable(model, name="capex_total", index_sets=sets.as_tuple("set_time_steps_yearly"),
            bounds=(0,np.inf), doc='total capex for installing all technologies in all locations at all times')
        # opex
        variables.add_variable(model, name="opex", index_sets=cls.create_custom_set(["set_technologies", "set_location", "set_time_steps_operation"], optimization_setup),
            bounds=(0,np.inf), doc="opex for operating technology at location l and time t")
        # total opex
        variables.add_variable(model, name="opex_total", index_sets=sets.as_tuple("set_time_steps_yearly"),
            bounds=(0,np.inf), doc="total opex for operating technology at location l and time t")
        # carbon emissions
        variables.add_variable(model, name="carbon_emissions_technology", index_sets=cls.create_custom_set(["set_technologies", "set_location", "set_time_steps_operation"], optimization_setup),
            doc="carbon emissions for operating technology at location l and time t")
        # total carbon emissions technology
        variables.add_variable(model, name="carbon_emissions_technology_total", index_sets=sets.as_tuple("set_time_steps_yearly"),
            doc="total carbon emissions for operating technology at location l and time t")

        # add pe.Vars of the child classes
        for subclass in cls.__subclasses__():
            subclass.construct_vars(optimization_setup)

    @classmethod
    def construct_constraints(cls, optimization_setup):
        """ constructs the pe.Constraints of the class <Technology>
        :param optimization_setup: The OptimizationSetup the element is part of """
        model = optimization_setup.model
        constraints = optimization_setup.constraints
        sets = optimization_setup.sets
        # construct pe.Constraints of the class <Technology>
        rules = TechnologyRules(optimization_setup)
        #  technology capacity_limit
        constraints.add_constraint_rule(model, name="constraint_technology_capacity_limit",
            index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup), rule=rules.constraint_technology_capacity_limit_rule,
            doc='limited capacity of  technology depending on loc and time')
        # minimum capacity
        constraints.add_constraint_rule(model, name="constraint_technology_min_capacity",
            index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup), rule=rules.constraint_technology_min_capacity_rule,
            doc='min capacity of technology that can be installed')
        # maximum capacity
        constraints.add_constraint_rule(model, name="constraint_technology_max_capacity",
            index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup), rule=rules.constraint_technology_max_capacity_rule,
            doc='max capacity of technology that can be installed')
        # construction period
        constraints.add_constraint_rule(model, name="constraint_technology_construction_time",
            index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup), rule=rules.constraint_technology_construction_time_rule,
            doc='lead time in which invested technology is constructed')
        # lifetime
        constraints.add_constraint_rule(model, name="constraint_technology_lifetime", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
            rule=rules.constraint_technology_lifetime_rule, doc='max capacity of  technology that can be installed')
        # limit diffusion rate
        constraints.add_constraint_rule(model, name="constraint_technology_diffusion_limit",
            index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup), rule=rules.constraint_technology_diffusion_limit_rule,
            doc="Limits the newly built capacity by the existing knowledge stock")
        # limit max load by installed capacity
        constraints.add_constraint_rule(model, name="constraint_capacity_factor", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_operation"], optimization_setup),
            rule=rules.constraint_capacity_factor_rule, doc='limit max load by installed capacity')
        # annual capex of having capacity
        constraints.add_constraint_rule(model, name="constraint_capex_yearly", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
            rule=rules.constraint_capex_yearly_rule, doc='annual capex of having capacity of technology.')
        # total capex of all technologies
        constraints.add_constraint_rule(model, name="constraint_capex_total", index_sets=sets.as_tuple("set_time_steps_yearly"), rule=rules.constraint_capex_total_rule,
            doc='total capex of all technology that can be installed.')
        # calculate opex
        constraints.add_constraint_rule(model, name="constraint_opex_technology", index_sets=cls.create_custom_set(["set_technologies", "set_location", "set_time_steps_operation"], optimization_setup),
            rule=rules.constraint_opex_technology_rule, doc="opex for each technology at each location and time step")
        # total opex of all technologies
        constraints.add_constraint_rule(model, name="constraint_opex_total", index_sets=sets.as_tuple("set_time_steps_yearly"), rule=rules.constraint_opex_total_rule, doc='total opex of all technology that are operated.')
        # carbon emissions of technologies
        constraints.add_constraint_rule(model, name="constraint_carbon_emissions_technology", index_sets=cls.create_custom_set(["set_technologies", "set_location", "set_time_steps_operation"], optimization_setup),
            rule=rules.constraint_carbon_emissions_technology_rule, doc="carbon emissions for each technology at each location and time step")
        # total carbon emissions of technologies
        constraints.add_constraint_rule(model, name="constraint_carbon_emissions_technology_total", index_sets=sets.as_tuple("set_time_steps_yearly"), rule=rules.constraint_carbon_emissions_technology_total_rule,
            doc="total carbon emissions for each technology at each location and time step")

        # disjunct if technology is on
        # FIXME: Actually implement disjunt shit
        # the disjunction variables
        tech_on_var = model.add_variables(name="tech_on_var", binary=True)
        tech_off_var = model.add_variables(name="tech_off_var", binary=True)
        model.add_constraints(tech_on_var + tech_off_var == 1, name="tech_on_off_cons")

        constraints.add_constraint_rule(model, name="disjunct_on_technology",
            index_sets=cls.create_custom_set(["set_technologies", "set_on_off", "set_capacity_types", "set_location", "set_time_steps_operation"], optimization_setup), rule=rules.disjunct_on_technology_rule,
            doc="disjunct to indicate that technology is on")
        # disjunct if technology is off
        constraints.add_constraint_rule(model, name="disjunct_off_technology",
            index_sets=cls.create_custom_set(["set_technologies", "set_on_off", "set_capacity_types", "set_location", "set_time_steps_operation"], optimization_setup), rule=rules.disjunct_off_technology_rule,
            doc="disjunct to indicate that technology is off")

        # add pe.Constraints of the child classes
        for subclass in cls.__subclasses__():
            logging.info(f"Construct pe.Constraints of {subclass.__name__}")
            subclass.construct_constraints(optimization_setup)


class TechnologyRules:
    """
    Rules for the Technology class
    """

    def __init__(self, optimization_setup):
        """
        Inits the rules
        :param optimization_setup: OptimizationSetup of the element
        """

        self.optimization_setup = optimization_setup
        placeholder_lhs = lp.expressions.ScalarLinearExpression((np.nan,), (-1,), lp.Model())
        self.emtpy_cons = lp.constraints.AnonymousScalarConstraint(placeholder_lhs, "=", np.nan)

    def disjunct_on_technology_rule(self, tech, capacity_type, loc, time):
        """definition of disjunct constraints if technology is On
        iterate through all subclasses to find corresponding implementation of disjunct constraints """
        for subclass in Technology.__subclasses__():
            if tech in self.optimization_setup.get_all_names_of_elements(subclass):
                # disjunct is defined in corresponding subclass
                subclass.disjunct_on_technology_rule(self.optimization_setup, tech, capacity_type, loc, time)
                return None

    def disjunct_off_technology_rule(self, tech, capacity_type, loc, time):
        """definition of disjunct constraints if technology is off
        iterate through all subclasses to find corresponding implementation of disjunct constraints """
        for subclass in Technology.__subclasses__():
            if tech in self.optimization_setup.get_all_names_of_elements(subclass):
                # disjunct is defined in corresponding subclass
                subclass.disjunct_off_technology_rule(self.optimization_setup, tech, capacity_type, loc, time)
                return None


    ### --- constraint rules --- ###
    # %% Constraint rules pre-defined in Technology class
    def constraint_technology_capacity_limit_rule(self, tech, capacity_type, loc, time):
        """limited capacity_limit of technology"""
        # get parameter object
        params = self.optimization_setup.parameters
        model = self.optimization_setup.model
        if params.capacity_limit_technology.loc[tech, capacity_type, loc] != np.inf:
            existing_capacities = Technology.get_available_existing_quantity(self.optimization_setup, tech, capacity_type, loc, time, type_existing_quantity="capacity")
            if existing_capacities < params.capacity_limit_technology.loc[tech, capacity_type, loc]:
                return (model.variables["capacity"][tech, capacity_type, loc, time] <= params.capacity_limit_technology.loc[tech, capacity_type, loc].item())
            else:
                return (model.variables["built_capacity"][tech, capacity_type, loc, time] == 0)
        else:
            return self.emtpy_cons

    def constraint_technology_min_capacity_rule(self, tech, capacity_type, loc, time):
        """ min capacity expansion of technology."""
        # get parameter object
        params = self.optimization_setup.parameters
        model = self.optimization_setup.model
        if params.min_built_capacity.loc[tech, capacity_type] != 0:
            return (params.min_built_capacity.loc[tech, capacity_type].item() * model.variables["install_technology"][tech, capacity_type, loc, time]
                    - model.variables["built_capacity"][tech, capacity_type, loc, time]
                    <= 0)
        else:
            return self.emtpy_cons

    def constraint_technology_max_capacity_rule(self, tech, capacity_type, loc, time):
        """max capacity expansion of technology"""
        # get parameter object
        params = self.optimization_setup.parameters
        model = self.optimization_setup.model
        if params.max_built_capacity.loc[tech, capacity_type] != np.inf:
            return (params.max_built_capacity.loc[tech, capacity_type].item() * model.variables["install_technology"][tech, capacity_type, loc, time]
                    - model.variables["built_capacity"][tech, capacity_type, loc, time]
                    >= 0)
        else:
            return self.emtpy_cons

    def constraint_technology_construction_time_rule(self, tech, capacity_type, loc, time):
        """ construction time of technology, i.e., time that passes between investment and availability"""
        # get parameter object
        params = self.optimization_setup.parameters
        model = self.optimization_setup.model
        sets = self.optimization_setup.sets
        start_time_step, _ = Technology.get_start_end_time_of_period(self.optimization_setup, tech, time, period_type="construction_time", clip_to_first_time_step=False)
        if start_time_step in sets["set_time_steps_yearly"]:
            return (model.variables["built_capacity"][tech, capacity_type, loc, time]
                    - model.variables["invested_capacity"][tech, capacity_type, loc, start_time_step]
                    == 0)
        elif start_time_step in sets["set_time_steps_yearly_entire_horizon"]:
            return (model.variables["built_capacity"][tech, capacity_type, loc, time]
                    == params.existing_invested_capacity.loc[tech, capacity_type, loc, start_time_step].item())
        else:
            return (model.variables["built_capacity"][tech, capacity_type, loc, time]
                    == 0)

    def constraint_technology_lifetime_rule(self, tech, capacity_type, loc, time):
        """limited lifetime of the technologies"""
        # determine existing capacities
        model = self.optimization_setup.model
        existing_capacities = Technology.get_available_existing_quantity(self.optimization_setup, tech, capacity_type, loc, time, type_existing_quantity="capacity")
        return (model.variables["capacity"][tech, capacity_type, loc, time]
                - sum(model.variables["built_capacity"][tech, capacity_type, loc, previous_time] for previous_time in Technology.get_lifetime_range(self.optimization_setup, tech, time))
                == existing_capacities)

    def constraint_technology_diffusion_limit_rule(self, tech, capacity_type, loc, time):
        """limited technology diffusion based on the existing capacity in the previous year """
        # get parameter object
        params = self.optimization_setup.parameters
        model = self.optimization_setup.model
        sets = self.optimization_setup.sets
        interval_between_years = self.optimization_setup.system["interval_between_years"]
        unbounded_market_share = self.optimization_setup.system["unbounded_market_share"]
        knowledge_depreciation_rate = self.optimization_setup.system["knowledge_depreciation_rate"]
        knowledge_spillover_rate = self.optimization_setup.system["knowledge_spillover_rate"]
        reference_carrier = sets["set_reference_carriers"][tech][0]
        if params.max_diffusion_rate.loc[tech, time] != np.inf:
            if tech in sets["set_transport_technologies"]:
                set_locations = sets["set_edges"]
                set_technology = sets["set_transport_technologies"]
            else:
                set_locations = sets["set_nodes"]
                if tech in sets["set_conversion_technologies"]:
                    set_technology = sets["set_conversion_technologies"]
                else:
                    set_technology = sets["set_storage_technologies"]
            # add built capacity of entire previous horizon
            if params.construction_time_technology.loc[tech] > 0:
                # if technology has lead time, restrict to current capacity
                end_time = time
            else:
                # else, to capacity in previous time step
                end_time = time - 1

            range_time = range(sets["set_time_steps_yearly"][0], end_time + 1)
            # actual years between first invest time step and end_time
            delta_time = interval_between_years * (end_time - sets["set_time_steps_yearly"][0])
            # sum up all existing capacities that ever existed and convert to knowledge stock
            total_capacity_knowledge_param = sum((params.existing_capacity.loc[tech, capacity_type, loc, existing_time].item() # add spillover from other regions
                                                  + sum(params.existing_capacity.loc[tech, capacity_type, other_loc, existing_time].item() * knowledge_spillover_rate
                                                        for other_loc in set_locations if other_loc != loc)) * (1 - knowledge_depreciation_rate) ** (delta_time + params.lifetime_technology.loc[tech].item() - params.lifetime_existing_technology.loc[tech, loc, existing_time].item())
                                                  for existing_time in sets["set_existing_technologies"][tech])
            total_capacity_knowledge_var = (sum((model.variables["built_capacity"][tech, capacity_type, loc, horizon_time] # add spillover from other regions
                                                 # add spillover from other regions
                                                + sum(model.variables["built_capacity"][tech, capacity_type, loc, horizon_time] * knowledge_spillover_rate for other_loc in set_locations if other_loc != loc)) * (1 - knowledge_depreciation_rate) ** (interval_between_years * (end_time - horizon_time))
                                                for horizon_time in range_time))

            total_capacity_all_techs_param = sum(Technology.get_available_existing_quantity(self.optimization_setup, other_tech, capacity_type, loc, time, type_existing_quantity="capacity")
                                                 for other_tech in set_technology if sets["set_reference_carriers"][other_tech][0] == reference_carrier)
            total_capacity_all_techs_var = sum(sum(model.variables["built_capacity"][other_tech, capacity_type, loc, previous_time]
                                                   for previous_time in Technology.get_lifetime_range(self.optimization_setup, tech, end_time))
                                               for other_tech in set_technology if sets["set_reference_carriers"][other_tech][0] == reference_carrier)

            # build the lhs (some terms might be 0)
            lhs = model.variables["invested_capacity"][tech, capacity_type, loc, time]
            if not isinstance(total_capacity_knowledge_var, (int, float)):
                lhs = lhs - ((1 + params.max_diffusion_rate.loc[tech, time].item()) ** interval_between_years - 1) * total_capacity_knowledge_var
            if not isinstance(total_capacity_all_techs_var, (float, int)):
                lhs = lhs - unbounded_market_share * total_capacity_all_techs_var

            return (lhs
                    <= ((1 + params.max_diffusion_rate.loc[tech, time].item()) ** interval_between_years - 1) * total_capacity_knowledge_param # add initial market share until which the diffusion rate is unbounded
                    + unbounded_market_share * total_capacity_all_techs_param)
        else:
            return self.emtpy_cons

    def constraint_capex_yearly_rule(self, tech, capacity_type, loc, year):
        """ aggregates the capex of built capacity and of existing capacity """
        system = self.optimization_setup.system
        model = self.optimization_setup.model
        sets = self.optimization_setup.sets
        discount_rate = self.optimization_setup.analysis["discount_rate"]
        return (model.variables["capex_yearly"][tech, capacity_type, loc, year]
                - (1 + discount_rate) ** (system["interval_between_years"] * (year - sets["set_time_steps_yearly"][0]))
                * (sum(model.variables["capex"][tech, capacity_type, loc, time] * (1 / (1 + discount_rate)) ** (system["interval_between_years"] * (time - sets["set_time_steps_yearly"][0]))
                       for time in Technology.get_lifetime_range(self.optimization_setup, tech, year, time_step_type="yearly")))
                == Technology.get_available_existing_quantity(self.optimization_setup, tech, capacity_type, loc, year, type_existing_quantity="capex", time_step_type="yearly"))

    def constraint_capex_total_rule(self, year):
        """ sums over all technologies to calculate total capex """
        model = self.optimization_setup.model
        return (model.variables["capex_total"][year]
                - sum(model.variables["capex_yearly"][tech, capacity_type, loc, year] for tech, capacity_type, loc in Element.create_custom_set(["set_technologies", "set_capacity_types", "set_location"], self.optimization_setup)[0])
                == 0)

    def constraint_opex_technology_rule(self, tech, loc, time):
        """ calculate opex of each technology"""
        # get parameter object
        params = self.optimization_setup.parameters
        model = self.optimization_setup.model
        sets = self.optimization_setup.sets
        reference_carrier = sets["set_reference_carriers"][tech][0]
        if tech in sets["set_conversion_technologies"]:
            if reference_carrier in sets["set_input_carriers"][tech]:
                reference_flow = model.variables["input_flow"][tech, reference_carrier, loc, time]
            else:
                reference_flow = model.variables["output_flow"][tech, reference_carrier, loc, time]
        elif tech in sets["set_transport_technologies"]:
            reference_flow = model.variables["carrier_flow"][tech, loc, time]
        else:
            reference_flow = model.variables["carrier_flow_charge"][tech, loc, time] + model.variables["carrier_flow_discharge"][tech, loc, time]
        return (model.variables["opex"][tech, loc, time]
                - params.opex_specific.loc[tech, loc, time].item() * reference_flow
                == 0)

    def constraint_carbon_emissions_technology_rule(self, tech, loc, time):
        """ calculate carbon emissions of each technology"""
        # get parameter object
        params = self.optimization_setup.parameters
        model = self.optimization_setup.model
        sets = self.optimization_setup.sets
        reference_carrier = sets["set_reference_carriers"][tech][0]
        if tech in sets["set_conversion_technologies"]:
            if reference_carrier in sets["set_input_carriers"][tech]:
                reference_flow = model.variables["input_flow"][tech, reference_carrier, loc, time]
            else:
                reference_flow = model.variables["output_flow"][tech, reference_carrier, loc, time]
        elif tech in sets["set_transport_technologies"]:
            reference_flow = model.variables["carrier_flow"][tech, loc, time]
        else:
            reference_flow = model.variables["carrier_flow_charge"][tech, loc, time] + model.variables["carrier_flow_discharge"][tech, loc, time]
        return (model.variables["carbon_emissions_technology"][tech, loc, time]
                - params.carbon_intensity_technology.loc[tech, loc].item() * reference_flow
                == 0)

    def constraint_carbon_emissions_technology_total_rule(self, year):
        """ calculate total carbon emissions of each technology"""
        # get parameter object
        params = self.optimization_setup.parameters
        model = self.optimization_setup.model
        return (model.variables["carbon_emissions_technology_total"][year]
                - sum(sum(model.variables["carbon_emissions_technology"][tech, loc, time] * params.time_steps_operation_duration.loc[tech, time].item()
                        for time in self.optimization_setup.energy_system.time_steps.get_time_steps_year2operation(tech, year))
                    for tech, loc in Element.create_custom_set(["set_technologies", "set_location"], self.optimization_setup)[0])
                == 0)

    def constraint_opex_total_rule(self, year):
        """ sums over all technologies to calculate total opex """
        # get parameter object
        params = self.optimization_setup.parameters
        model = self.optimization_setup.model
        return (model.variables["opex_total"][year]
                - sum(sum(model.variables["opex"][tech, loc, time] * params.time_steps_operation_duration.loc[tech, time].item()
                        for time in self.optimization_setup.energy_system.time_steps.get_time_steps_year2operation(tech, year))
                    for tech, loc in Element.create_custom_set(["set_technologies", "set_location"], self.optimization_setup)[0])
                == 0)

    def constraint_capacity_factor_rule(self, tech, capacity_type, loc, time):
        """Load is limited by the installed capacity and the maximum load factor"""
        # get parameter object
        params = self.optimization_setup.parameters
        model = self.optimization_setup.model
        sets = self.optimization_setup.sets
        reference_carrier = sets["set_reference_carriers"][tech][0]
        # get invest time step
        time_step_year = self.optimization_setup.energy_system.time_steps.convert_time_step_operation2year(tech, time)
        # conversion technology
        if tech in sets["set_conversion_technologies"]:
            if reference_carrier in sets["set_input_carriers"][tech]:
                return (model.variables["capacity"][tech, capacity_type, loc, time_step_year] * params.max_load.loc[tech, capacity_type, loc, time].item()
                        - model.variables["input_flow"][tech, reference_carrier, loc, time]
                        >= 0)
            else:
                return (model.variables["capacity"][tech, capacity_type, loc, time_step_year] * params.max_load.loc[tech, capacity_type, loc, time].item()
                        - model.variables["output_flow"][tech, reference_carrier, loc, time]
                        >= 0)
        # transport technology
        elif tech in sets["set_transport_technologies"]:
            return (model.variables["capacity"][tech, capacity_type, loc, time_step_year] * params.max_load.loc[tech, capacity_type, loc, time].item()
                    - model.variables["carrier_flow"][tech, loc, time]
                    >= 0)
        # storage technology
        elif tech in sets["set_storage_technologies"]:
            system = self.optimization_setup.system
            # if limit power
            if capacity_type == system["set_capacity_types"][0]:
                return (model.variables["capacity"][tech, capacity_type, loc, time_step_year] * params.max_load.loc[tech, capacity_type, loc, time].item()
                        - model.variables["carrier_flow_charge"][tech, loc, time]
                        - model.variables["carrier_flow_discharge"][tech, loc, time]
                        >= 0)
            # TODO integrate level storage here as well
            else:
                return self.emtpy_cons  # if limit energy  # else:  #     return (model.capacity[tech,capacity_type, loc, time_step_year] * model.max_load[tech,capacity_type, loc, time] >= model.levelStorage[tech,loc,time])
