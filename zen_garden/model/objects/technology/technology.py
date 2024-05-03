"""
:Title:          ZEN-GARDEN
:Created:        October-2021
:Authors:        Alissa Ganter (aganter@ethz.ch),
                Jacob Mannhardt (jmannhardt@ethz.ch)
:Organization:   Laboratory of Reliability and Risk Engineering, ETH Zurich

Class defining the parameters, variables and constraints that hold for all technologies.
The class takes the abstract optimization model as an input, and returns the parameters, variables and
constraints that hold for all technologies.
"""
import cProfile
import itertools
import logging

import linopy as lp
import numpy as np
import pandas as pd
import xarray as xr

from zen_garden.utils import lp_sum
from ..component import ZenIndex, IndexSet
from ..element import Element, GenericRule

class Technology(Element):
    """
    Class defining the parameters, variables and constraints that hold for all technologies.
    """
    # set label
    label = "set_technologies"
    location_type = None

    def __init__(self, technology: str, optimization_setup):
        """init generic technology object

        :param technology: technology that is added to the model
        :param optimization_setup: The OptimizationSetup the element is part of """

        super().__init__(technology, optimization_setup)

    def store_carriers(self):
        """ retrieves and stores information on reference """
        self.reference_carrier = self.data_input.extract_carriers(carrier_type="reference_carrier")
        self.energy_system.set_technology_of_carrier(self.name, self.reference_carrier)

    def store_input_data(self):
        """ retrieves and stores input data for element as attributes. Each Child class overwrites method to store different attributes """
        # store scenario dict
        super().store_scenario_dict()
        # set attributes of technology
        set_location = self.location_type
        self.capacity_addition_min = self.data_input.extract_input_data("capacity_addition_min", index_sets=[], unit_category={"energy_quantity": 1, "time": -1})
        self.capacity_addition_max = self.data_input.extract_input_data("capacity_addition_max", index_sets=[], unit_category={"energy_quantity": 1, "time": -1})
        self.capacity_addition_unbounded = self.data_input.extract_input_data("capacity_addition_unbounded", index_sets=[], unit_category={"energy_quantity": 1, "time": -1})
        self.lifetime = self.data_input.extract_input_data("lifetime", index_sets=[], unit_category={})
        self.construction_time = self.data_input.extract_input_data("construction_time", index_sets=[], unit_category={})
        # maximum diffusion rate
        self.max_diffusion_rate = self.data_input.extract_input_data("max_diffusion_rate", index_sets=["set_time_steps_yearly"], time_steps="set_time_steps_yearly", unit_category={})

        # add all raw time series to dict
        self.raw_time_series = {}
        self.raw_time_series["min_load"] = self.data_input.extract_input_data("min_load", index_sets=[set_location, "set_time_steps"], time_steps="set_base_time_steps_yearly", unit_category={})
        self.raw_time_series["max_load"] = self.data_input.extract_input_data("max_load", index_sets=[set_location, "set_time_steps"], time_steps="set_base_time_steps_yearly", unit_category={})
        self.raw_time_series["opex_specific_variable"] = self.data_input.extract_input_data("opex_specific_variable", index_sets=[set_location, "set_time_steps"], time_steps="set_base_time_steps_yearly", unit_category={"money": 1, "energy_quantity": -1})
        # non-time series input data
        self.capacity_limit = self.data_input.extract_input_data("capacity_limit", index_sets=[set_location, "set_time_steps_yearly"], time_steps="set_time_steps_yearly", unit_category={"energy_quantity": 1, "time": -1})
        self.carbon_intensity_technology = self.data_input.extract_input_data("carbon_intensity_technology", index_sets=[set_location], unit_category={"emissions": 1, "energy_quantity": -1})
        # extract existing capacity
        self.set_technologies_existing = self.data_input.extract_set_technologies_existing()
        self.capacity_existing = self.data_input.extract_input_data("capacity_existing", index_sets=[set_location, "set_technologies_existing"], unit_category={"energy_quantity": 1, "time": -1})
        self.capacity_investment_existing = self.data_input.extract_input_data("capacity_investment_existing", index_sets=[set_location, "set_time_steps_yearly"], time_steps="set_time_steps_yearly", unit_category={"energy_quantity": 1, "time": -1})
        self.lifetime_existing = self.data_input.extract_lifetime_existing("capacity_existing", index_sets=[set_location, "set_technologies_existing"])

    def calculate_capex_of_capacities_existing(self, storage_energy=False):
        """ this method calculates the annualized capex of the existing capacities

        :param storage_energy: boolean if energy storage
        :return: capex of existing capacities
        """
        if self.__class__.__name__ == "StorageTechnology":
            if storage_energy:
                capacities_existing = self.capacity_existing_energy
            else:
                capacities_existing = self.capacity_existing
            capex_capacity_existing = capacities_existing.to_frame().apply(
                lambda _capacity_existing: self.calculate_capex_of_single_capacity(_capacity_existing.squeeze(), _capacity_existing.name, storage_energy), axis=1)
        else:
            capacities_existing = self.capacity_existing
            capex_capacity_existing = capacities_existing.to_frame().apply(lambda _capacity_existing: self.calculate_capex_of_single_capacity(_capacity_existing.squeeze(), _capacity_existing.name), axis=1)
        return capex_capacity_existing

    def calculate_capex_of_single_capacity(self, *args):
        """ this method calculates the annualized capex of the existing capacities. Is implemented in child class

        :param args: arguments
        """
        raise NotImplementedError

    def calculate_fraction_of_year(self):
        """calculate fraction of year"""
        # only account for fraction of year
        fraction_year = self.optimization_setup.system["unaggregated_time_steps_per_year"] / self.optimization_setup.system["total_hours_per_year"]
        return fraction_year

    def add_new_capacity_addition_tech(self, capacity_addition: pd.Series, capex: pd.Series, step_horizon: int):
        """ adds the newly built capacity to the existing capacity

        :param capacity_addition: pd.Series of newly built capacity of technology
        :param capex: pd.Series of capex of newly built capacity of technology
        :param step_horizon: current horizon step """
        system = self.optimization_setup.system
        # reduce lifetime of existing capacities and add new remaining lifetime
        self.lifetime_existing = (self.lifetime_existing - system["interval_between_years"]).clip(lower=0)
        # new capacity
        new_capacity_addition = capacity_addition[step_horizon]
        new_capex = capex[step_horizon]
        # if at least one value unequal to zero
        if not (new_capacity_addition == 0).all():
            # add new index to set_technologies_existing
            index_new_technology = max(self.set_technologies_existing) + 1
            self.set_technologies_existing = np.append(self.set_technologies_existing, index_new_technology)
            # add new remaining lifetime
            lifetime = self.lifetime_existing.unstack()
            lifetime[index_new_technology] = self.lifetime[0] - system["interval_between_years"]
            self.lifetime_existing = lifetime.stack()

            for type_capacity in list(set(new_capacity_addition.index.get_level_values(0))):
                # if power
                if type_capacity == system["set_capacity_types"][0]:
                    energy_string = ""
                # if energy
                else:
                    energy_string = "_energy"
                capacity_existing = getattr(self, "capacity_existing" + energy_string)
                capex_capacity_existing = getattr(self, "capex_capacity_existing" + energy_string)
                # add new existing capacity
                capacity_existing = capacity_existing.unstack()
                capacity_existing[index_new_technology] = new_capacity_addition.loc[type_capacity]
                setattr(self, "capacity_existing" + energy_string, capacity_existing.stack())
                # calculate capex of existing capacity
                capex_capacity_existing = capex_capacity_existing.unstack()
                capex_capacity_existing[index_new_technology] = new_capex.loc[type_capacity]
                setattr(self, "capex_capacity_existing" + energy_string, capex_capacity_existing.stack())

    def add_new_capacity_investment(self, capacity_investment: pd.Series, step_horizon):
        """ adds the newly invested capacity to the list of invested capacity

        :param capacity_investment: pd.Series of newly built capacity of technology
        :param step_horizon: optimization time step """
        system = self.optimization_setup.system
        new_capacity_investment = capacity_investment[step_horizon]
        new_capacity_investment = new_capacity_investment.fillna(0)
        if not (new_capacity_investment == 0).all():
            for type_capacity in list(set(new_capacity_investment.index.get_level_values(0))):
                # if power
                if type_capacity == system["set_capacity_types"][0]:
                    energy_string = ""
                # if energy
                else:
                    energy_string = "_energy"
                capacity_investment_existing = getattr(self, "capacity_investment_existing" + energy_string)
                # add new existing invested capacity
                capacity_investment_existing = capacity_investment_existing.unstack()
                capacity_investment_existing[step_horizon] = new_capacity_investment.loc[type_capacity]
                setattr(self, "capacity_investment_existing" + energy_string, capacity_investment_existing.stack())

    ### --- classmethods
    @classmethod
    def get_available_existing_quantity(cls, optimization_setup, tech, capacity_type, loc, year, type_existing_quantity):
        """ returns existing quantity of 'tech', that is still available at invest time step 'time'.
        Either capacity or capex.

        :param optimization_setup: The OptimizationSetup the element is part of
        :param tech: name of technology
        :param capacity_type: type of capacity
        :param loc: location (node or edge) of existing capacity
        :param year: current yearly time step
        :param type_existing_quantity: capex or capacity
        :return existing_quantity: existing capacity or capex of existing capacity
        """
        params = optimization_setup.parameters.dict_parameters
        sets = optimization_setup.sets
        existing_quantity = 0
        if type_existing_quantity == "capacity":
            existing_variable = params.capacity_existing
        elif type_existing_quantity == "cost_capex":
            existing_variable = params.capex_capacity_existing
        else:
            raise KeyError(f"Wrong type of existing quantity {type_existing_quantity}")

        for id_capacity_existing in sets["set_technologies_existing"][tech]:
            is_existing = cls.get_if_capacity_still_existing(optimization_setup, tech, year, loc=loc, id_capacity_existing=id_capacity_existing)
            # if still available at first base time step, add to list
            if is_existing:
                existing_quantity += existing_variable[tech, capacity_type, loc, id_capacity_existing]
        return existing_quantity

    @classmethod
    def get_if_capacity_still_existing(cls,optimization_setup, tech, year,loc,id_capacity_existing):
        """
        returns boolean if capacity still exists at yearly time step 'year'.
        :param optimization_setup: The optimization setup to add everything
        :param tech: name of technology
        :param year: yearly time step
        :param loc: location
        :param id_capacity_existing: id of existing capacity
        :return: boolean if still existing
        """
        # get params and system
        params = optimization_setup.parameters.dict_parameters
        system = optimization_setup.system
        # get lifetime of existing capacity
        lifetime_existing = params.lifetime_existing[tech, loc, id_capacity_existing]
        lifetime = params.lifetime[tech]
        delta_lifetime = lifetime_existing - lifetime
        # reference year of current optimization horizon
        current_year_horizon = optimization_setup.energy_system.set_time_steps_yearly[0]
        if delta_lifetime >= 0:
            cutoff_year = (year-current_year_horizon)*system["interval_between_years"]
            return cutoff_year >= delta_lifetime
        else:
            cutoff_year = (year-current_year_horizon+1)*system["interval_between_years"]
            return cutoff_year <= lifetime_existing

    @classmethod
    def get_lifetime_range(cls, optimization_setup, tech, year):
        """ returns lifetime range of technology.

        :param optimization_setup: OptimizationSetup the technology is part of
        :param tech: name of the technology
        :param year: yearly time step
        :return: lifetime range of technology
        """
        first_lifetime_year = cls.get_first_lifetime_time_step(optimization_setup, tech, year)
        first_lifetime_year = max(first_lifetime_year, optimization_setup.sets["set_time_steps_yearly"][0])
        return range(first_lifetime_year, year + 1)

    @classmethod
    def get_first_lifetime_time_step(cls,optimization_setup,tech,year):
        """
        returns first lifetime time step of technology,
        i.e., the earliest time step in the past whose capacity is still available at the current time step
        :param optimization_setup: The optimization setup to add everything
        :param tech: name of technology
        :param year: yearly time step
        :return: first lifetime step
        """
        # get params and system
        params = optimization_setup.parameters.dict_parameters
        system = optimization_setup.system
        lifetime = params.lifetime[tech]
        # conservative estimate of lifetime (floor)
        del_lifetime = int(np.floor(lifetime/system["interval_between_years"])) - 1
        return year - del_lifetime

    @classmethod
    def get_investment_time_step(cls,optimization_setup,tech,year):
        """
        returns investment time step of technology, i.e., the time step in which the technology is invested considering the construction time
        :param optimization_setup: The optimization setup to add everything
        :param tech: name of technology
        :param year: yearly time step
        :return: investment time step
        """
        # get params and system
        params = optimization_setup.parameters.dict_parameters
        system = optimization_setup.system
        construction_time = params.construction_time[tech]
        # conservative estimate of construction time (ceil)
        del_construction_time = int(np.ceil(construction_time/system["interval_between_years"]))
        return year - del_construction_time

    ### --- classmethods to construct sets, parameters, variables, and constraints, that correspond to Technology --- ###
    @classmethod
    def construct_sets(cls, optimization_setup):
        """ constructs the pe.Sets of the class <Technology>

        :param optimization_setup: The OptimizationSetup """
        # construct the pe.Sets of the class <Technology>
        energy_system = optimization_setup.energy_system

        # conversion technologies
        optimization_setup.sets.add_set(name="set_conversion_technologies", data=energy_system.set_conversion_technologies,
                                        doc="Set of conversion technologies. Indexed by set_technologies")
        # retrofitting technologies
        optimization_setup.sets.add_set(name="set_retrofitting_technologies", data=energy_system.set_retrofitting_technologies,
                                        doc="Set of retrofitting technologies. Indexed by set_conversion_technologies")
        # transport technologies
        optimization_setup.sets.add_set(name="set_transport_technologies", data=energy_system.set_transport_technologies,
                                        doc="Set of transport technologies. Indexed by set_technologies")
        # storage technologies
        optimization_setup.sets.add_set(name="set_storage_technologies", data=energy_system.set_storage_technologies,
                                        doc="Set of storage technologies. Indexed by set_technologies")
        # existing installed technologies
        optimization_setup.sets.add_set(name="set_technologies_existing", data=optimization_setup.get_attribute_of_all_elements(cls, "set_technologies_existing"),
                                        doc="Set of existing technologies. Indexed by set_technologies",
                                        index_set="set_technologies")
        # reference carriers
        optimization_setup.sets.add_set(name="set_reference_carriers", data=optimization_setup.get_attribute_of_all_elements(cls, "reference_carrier"),
                                        doc="set of all reference carriers correspondent to a technology. Indexed by set_technologies",
                                        index_set="set_technologies")
        # add pe.Sets of the child classes
        for subclass in cls.__subclasses__():
            subclass.construct_sets(optimization_setup)

    @classmethod
    def construct_params(cls, optimization_setup):
        """ constructs the pe.Params of the class <Technology>

        :param optimization_setup: The OptimizationSetup """
        # construct pe.Param of the class <Technology>

        # existing capacity
        optimization_setup.parameters.add_parameter(name="capacity_existing", index_names=["set_technologies", "set_capacity_types", "set_location", "set_technologies_existing"], capacity_types=True, doc='Parameter which specifies the existing technology size', calling_class=cls)
        # existing capacity
        optimization_setup.parameters.add_parameter(name="capacity_investment_existing", index_names=["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly_entire_horizon"], capacity_types=True, doc='Parameter which specifies the size of the previously invested capacities', calling_class=cls)
        # minimum capacity addition
        optimization_setup.parameters.add_parameter(name="capacity_addition_min", index_names=["set_technologies", "set_capacity_types"], capacity_types=True, doc='Parameter which specifies the minimum capacity addition that can be installed', calling_class=cls)
        # maximum capacity addition
        optimization_setup.parameters.add_parameter(name="capacity_addition_max", index_names=["set_technologies", "set_capacity_types"], capacity_types=True, doc='Parameter which specifies the maximum capacity addition that can be installed', calling_class=cls)
        # unbounded capacity addition
        optimization_setup.parameters.add_parameter(name="capacity_addition_unbounded", index_names=["set_technologies"], doc='Parameter which specifies the unbounded capacity addition that can be added each year (only for delayed technology deployment)', calling_class=cls)
        # lifetime existing technologies
        optimization_setup.parameters.add_parameter(name="lifetime_existing", index_names=["set_technologies", "set_location", "set_technologies_existing"], doc='Parameter which specifies the remaining lifetime of an existing technology', calling_class=cls)
        # lifetime existing technologies
        optimization_setup.parameters.add_parameter(name="capex_capacity_existing", index_names=["set_technologies", "set_capacity_types", "set_location", "set_technologies_existing"], capacity_types=True, doc='Parameter which specifies the total capex of an existing technology which still has to be paid', calling_class=cls)
        # variable specific opex
        optimization_setup.parameters.add_parameter(name="opex_specific_variable", index_names=["set_technologies","set_location","set_time_steps_operation"], doc='Parameter which specifies the variable specific opex', calling_class=cls)
        # fixed specific opex
        optimization_setup.parameters.add_parameter(name="opex_specific_fixed", index_names=["set_technologies", "set_capacity_types","set_location","set_time_steps_yearly"], capacity_types=True, doc='Parameter which specifies the fixed annual specific opex', calling_class=cls)
        # lifetime newly built technologies
        optimization_setup.parameters.add_parameter(name="lifetime", index_names=["set_technologies"], doc='Parameter which specifies the lifetime of a newly built technology', calling_class=cls)
        # construction_time newly built technologies
        optimization_setup.parameters.add_parameter(name="construction_time", index_names=["set_technologies"], doc='Parameter which specifies the construction time of a newly built technology', calling_class=cls)
        # maximum diffusion rate, i.e., increase in capacity
        optimization_setup.parameters.add_parameter(name="max_diffusion_rate", index_names=["set_technologies", "set_time_steps_yearly"], doc="Parameter which specifies the maximum diffusion rate which is the maximum increase in capacity between investment steps", calling_class=cls)
        # capacity_limit of technologies
        optimization_setup.parameters.add_parameter(name="capacity_limit", index_names=["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], capacity_types=True, doc='Parameter which specifies the capacity limit of technologies', calling_class=cls)
        # minimum load relative to capacity
        optimization_setup.parameters.add_parameter(name="min_load", index_names=["set_technologies", "set_capacity_types", "set_location", "set_time_steps_operation"], capacity_types=True, doc='Parameter which specifies the minimum load of technology relative to installed capacity', calling_class=cls)
        # maximum load relative to capacity
        optimization_setup.parameters.add_parameter(name="max_load", index_names=["set_technologies", "set_capacity_types", "set_location", "set_time_steps_operation"], capacity_types=True, doc='Parameter which specifies the maximum load of technology relative to installed capacity', calling_class=cls)
        # carbon intensity
        optimization_setup.parameters.add_parameter(name="carbon_intensity_technology", index_names=["set_technologies", "set_location"], doc='Parameter which specifies the carbon intensity of each technology', calling_class=cls)
        # calculate additional existing parameters
        optimization_setup.parameters.add_parameter(name="existing_capacities", data=cls.get_existing_quantity(optimization_setup, type_existing_quantity="capacity"),
                                                    doc="Parameter which specifies the total available capacity of existing technologies at the beginning of the optimization", calling_class=cls)
        optimization_setup.parameters.add_parameter(name="existing_capex", data=cls.get_existing_quantity(optimization_setup,type_existing_quantity="cost_capex"),
                                                    doc="Parameter which specifies the total capex of existing technologies at the beginning of the optimization", calling_class=cls)
        # add pe.Param of the child classes
        for subclass in cls.__subclasses__():
            subclass.construct_params(optimization_setup)

    @classmethod
    def construct_vars(cls, optimization_setup):
        """ constructs the pe.Vars of the class <Technology>
        :param optimization_setup: The OptimizationSetup """

        model = optimization_setup.model
        variables = optimization_setup.variables
        sets = optimization_setup.sets

        def capacity_bounds(tech, capacity_type, loc, time):
            """ 
            # TODO: This could be vectorized
            return bounds of capacity for bigM expression
            :param tech: tech index
            :param capacity_type: either power or energy
            :param loc: location of capacity
            :param time: investment time step
            :return bounds: bounds of capacity"""
            # bounds only needed for Big-M formulation, thus if any technology is modeled with on-off behavior
            if tech in techs_on_off:
                system = optimization_setup.system
                params = optimization_setup.parameters.dict_parameters
                if capacity_type == system["set_capacity_types"][0]:
                    energy_string = ""
                else:
                    energy_string = "_energy"
                capacity_existing = getattr(params, "capacity_existing" + energy_string)
                capacity_addition_max = getattr(params, "capacity_addition_max" + energy_string)
                capacity_limit = getattr(params, "capacity_limit" + energy_string)
                capacities_existing = 0
                for id_technology_existing in sets["set_technologies_existing"][tech]:
                    if params.lifetime_existing[tech, loc, id_technology_existing] > params.lifetime[tech]:
                        if time > params.lifetime_existing[tech, loc, id_technology_existing] - params.lifetime[tech]:
                            capacities_existing += capacity_existing[tech, capacity_type, loc, id_technology_existing]
                    elif time <= params.lifetime_existing[tech, loc, id_technology_existing] + 1:
                        capacities_existing += capacity_existing[tech, capacity_type, loc, id_technology_existing]

                capacity_addition_max = len(sets["set_time_steps_yearly"]) * capacity_addition_max[tech, capacity_type]
                max_capacity_limit = capacity_limit[tech, capacity_type, loc, time]
                bound_capacity = min(capacity_addition_max + capacities_existing, max_capacity_limit + capacities_existing)
                return 0, bound_capacity
            else:
                return 0, np.inf

        # bounds only needed for Big-M formulation, thus if any technology is modeled with on-off behavior
        techs_on_off = cls.create_custom_set(["set_technologies", "set_on_off"], optimization_setup)[0]
        # construct pe.Vars of the class <Technology>
        # capacity technology
        variables.add_variable(model, name="capacity", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
            bounds=capacity_bounds, doc='size of installed technology at location l and time t', unit_category={"energy_quantity": 1, "time": -1})
        # capacity technology before current year
        variables.add_variable(model, name="capacity_previous", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
            bounds=capacity_bounds, doc='size of installed technology at location l and BEFORE time t', unit_category={"energy_quantity": 1, "time": -1})
        # built_capacity technology
        variables.add_variable(model, name="capacity_addition", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
            bounds=(0,np.inf), doc='size of built technology (invested capacity after construction) at location l and time t', unit_category={"energy_quantity": 1, "time": -1})
        # invested_capacity technology
        variables.add_variable(model, name="capacity_investment", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
            bounds=(0,np.inf), doc='size of invested technology at location l and time t', unit_category={"energy_quantity": 1, "time": -1})
        # capex of building capacity
        variables.add_variable(model, name="cost_capex", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
            bounds=(0,np.inf), doc='capex for building technology at location l and time t', unit_category={"money": 1})
        # annual capex of having capacity
        variables.add_variable(model, name="capex_yearly", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
            bounds=(0,np.inf), doc='annual capex for having technology at location l', unit_category={"money": 1})
        # total capex
        variables.add_variable(model, name="cost_capex_total", index_sets=sets["set_time_steps_yearly"],
            bounds=(0,np.inf), doc='total capex for installing all technologies in all locations at all times', unit_category={"money": 1})
        # opex
        variables.add_variable(model, name="cost_opex", index_sets=cls.create_custom_set(["set_technologies", "set_location", "set_time_steps_operation"], optimization_setup),
            bounds=(0,np.inf), doc="opex for operating technology at location l and time t", unit_category={"money": 1, "time": -1})
        # total opex
        variables.add_variable(model, name="cost_opex_total", index_sets=sets["set_time_steps_yearly"],
            bounds=(0,np.inf), doc="total opex all technologies and locations in year y", unit_category={"money": 1})
        # yearly opex
        variables.add_variable(model, name="opex_yearly", index_sets=cls.create_custom_set(["set_technologies", "set_location", "set_time_steps_yearly"], optimization_setup),
            bounds=(0,np.inf), doc="yearly opex for operating technology at location l and year y", unit_category={"money": 1})
        # carbon emissions
        variables.add_variable(model, name="carbon_emissions_technology", index_sets=cls.create_custom_set(["set_technologies", "set_location", "set_time_steps_operation"], optimization_setup),
            doc="carbon emissions for operating technology at location l and time t", unit_category={"emissions": 1, "time": -1})
        # total carbon emissions technology
        variables.add_variable(model, name="carbon_emissions_technology_total", index_sets=sets["set_time_steps_yearly"],
            doc="total carbon emissions for operating technology at location l and time t", unit_category={"emissions": 1})

        # install technology
        # Note: binary variables are written into the lp file by linopy even if they are not relevant for the optimization,
        # which makes all problems MIPs. Therefore, we only add binary variables, if really necessary. Gurobi can handle this
        # by noting that the binary variables are not part of the model, however, only if there are no binary variables at all,
        # it is possible to get the dual values of the constraints.
        mask = cls._technology_installation_mask(optimization_setup)
        if mask.any():
            variables.add_variable(model, name="technology_installation", index_sets=cls.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup),
                                   binary=True, doc='installment of a technology at location l and time t', mask=mask, unit_category=None)

        # add pe.Vars of the child classes
        for subclass in cls.__subclasses__():
            subclass.construct_vars(optimization_setup)

    @classmethod
    def construct_constraints(cls, optimization_setup):
        """ constructs the pe.Constraints of the class <Technology>

        :param optimization_setup: The OptimizationSetup """
        model = optimization_setup.model
        constraints = optimization_setup.constraints
        # construct pe.Constraints of the class <Technology>
        rules = TechnologyRules(optimization_setup)
        #  technology capacity_limit
        rules.constraint_technology_capacity_limit()
        # doc = 'limited capacity of  technology depending on loc and time'
        # constraints.add_constraint(name="constraint_technology_capacity_limit",
        #                                  constraint=rules.constraint_technology_capacity_limit(),
        #                                  doc='limited capacity of  technology depending on loc and time')
        # minimum capacity
        rules.constraint_technology_min_capacity_addition()
        # doc = 'min capacity of technology that can be installed'
        # constraints.add_constraint(name="constraint_technology_min_capacity_addition",
        #                                  constraint=rules.constraint_technology_min_capacity_addition(),
        #                                  doc='min capacity of technology that can be installed')
        # maximum capacity
        rules.constraint_technology_max_capacity_addition()
        # doc = 'max capacity of technology that can be installed'
        # constraints.add_constraint(name="constraint_technology_max_capacity_addition",
        #                                  constraint=rules.constraint_technology_max_capacity_addition(),
        #                                  doc='max capacity of technology that can be installed')
        # construction period
        rules.constraint_technology_construction_time()
        # doc = 'lead time in which invested technology is constructed'
        # constraints.add_constraint(name="constraint_technology_construction_time",
        #                                  constraint=rules.constraint_technology_construction_time(),
        #                                  doc='lead time in which invested technology is constructed')
        # lifetime
        rules.constraint_technology_lifetime()
        # doc = 'calculate all existing capacity in certain year'
        # constraints.add_constraint(name="constraint_technology_lifetime",
        #                                  constraint=rules.constraint_technology_lifetime(),
        #                                  doc='calculate all existing capacity in certain year')
        # limit diffusion rate
        rules.constraint_technology_diffusion_limit()
        # doc = 'limit the newly built capacity by the existing knowledge stock'
        # constraints.add_constraint(name="constraint_technology_diffusion_limit",
        #                                  constraint=rules.constraint_technology_diffusion_limit(),
        #                                  doc="limit the newly built capacity by the existing knowledge stock")
        # limit diffusion rate total
        # rules.constraint_technology_diffusion_limit_total()
        # doc = 'limit the newly built capacity by the existing knowledge stock for the entire energy system'
        # constraints.add_constraint(name="constraint_technology_diffusion_limit_total",
        #                                  constraint=rules.constraint_technology_diffusion_limit_total(),
        #                                  doc="limit the newly built capacity by the existing knowledge stock for the entire energy system")
        # limit max load by installed capacity
        # rules.constraint_capacity_factor()
        # doc = 'limit max load by installed capacity'
        # constraints.add_constraint(name="constraint_capacity_factor",
        #                                  constraint=rules.constraint_capacity_factor(),
        #                                  doc='limit max load by installed capacity')
        # annual capex of having capacity
        rules.constraint_capex_yearly()
        # doc = 'annual capex of having capacity of technology.'
        # constraints.add_constraint(name="constraint_capex_yearly",
        #                                  constraint=rules.constraint_capex_yearly(),
        #                                  doc='annual capex of having capacity of technology.')
        # total capex of all technologies
        rules.constraint_cost_capex_total()
        # doc = 'total capex of all technology that can be installed.'
        # constraints.add_constraint(name="constraint_cost_capex_total", constraint=rules.constraint_cost_capex_total(),
        #     doc='total capex of all technology that can be installed.')
        # calculate opex
        # rules.constraint_opex_technology()
        # doc = 'opex for each technology at each location and time step'
        # constraints.add_constraint(name="constraint_opex_technology",
        #                                  constraint=rules.constraint_opex_technology(),
        #                                  doc="opex for each technology at each location and time step")
        # yearly opex
        rules.constraint_opex_yearly()
        # doc = 'total opex of all technology that are operated.'
        # constraints.add_constraint(name="constraint_opex_yearly",
        #                                  constraint=rules.constraint_opex_yearly(),
        #                                  doc='total opex of all technology that are operated.')
        # total opex of all technologies
        rules.constraint_cost_opex_total()
        # doc = 'total opex of all technology that are operated.'
        # constraints.add_constraint(name="constraint_cost_opex_total", constraint=rules.constraint_cost_opex_total(), doc='total opex of all technology that are operated.')
        # carbon emissions of technologies
        # rules.constraint_carbon_emissions_technology()
        # doc = 'carbon emissions for each technology at each location and time step'
        # constraints.add_constraint(name="constraint_carbon_emissions_technology",
        #                                  constraint=rules.constraint_carbon_emissions_technology(),
        #                                  doc="carbon emissions for each technology at each location and time step")
        # total carbon emissions of technologies
        rules.constraint_carbon_emissions_technology_total()
        # doc = 'total carbon emissions for each technology at each location and time step'
        # constraints.add_constraint(name="constraint_carbon_emissions_technology_total", constraint=rules.constraint_carbon_emissions_technology_total(),
        #                                  doc="total carbon emissions for each technology at each location and time step")

        # disjunct if technology is on
        # the disjunction variables
        variables = optimization_setup.variables
        index_vals, _ = cls.create_custom_set(["set_technologies", "set_on_off", "set_capacity_types", "set_location", "set_time_steps_operation"], optimization_setup)
        index_names = ["on_off_technologies", "on_off_capacity_types", "on_off_locations", "on_off_time_steps_operation"]
        variables.add_variable(model, name="tech_on_var",
                               index_sets=(index_vals, index_names),
                               doc="Binary variable which equals 1 when technology is switched on at location l and time t", binary=True, unit_category=None)
        variables.add_variable(model, name="tech_off_var",
                               index_sets=(index_vals, index_names),
                               doc="Binary variable which equals 1 when technology is switched off at location l and time t", binary=True, unit_category=None)
        model.add_constraints(model.variables["tech_on_var"] + model.variables["tech_off_var"] == 1, name="tech_on_off_cons")
        n_cons = model.constraints.ncons

        # disjunct if technology is on
        constraints.add_constraint_rule(model, name="disjunct_on_technology",
            index_sets=cls.create_custom_set(["set_technologies", "set_on_off", "set_capacity_types", "set_location", "set_time_steps_operation"], optimization_setup), rule=rules.disjunct_on_technology,
            doc="disjunct to indicate that technology is on")
        # disjunct if technology is off
        constraints.add_constraint_rule(model, name="disjunct_off_technology",
            index_sets=cls.create_custom_set(["set_technologies", "set_on_off", "set_capacity_types", "set_location", "set_time_steps_operation"], optimization_setup), rule=rules.disjunct_off_technology,
            doc="disjunct to indicate that technology is off")

        # if nothing was added we can remove the tech vars again
        if model.constraints.ncons == n_cons:
            model.constraints.remove("tech_on_off_cons")
            model.variables.remove("tech_on_var")
            model.variables.remove("tech_off_var")

        # add pe.Constraints of the child classes
        for subclass in cls.__subclasses__():
            logging.info(f"Construct pe.Constraints of {subclass.__name__}")
            subclass.construct_constraints(optimization_setup)

    @classmethod
    def _technology_installation_mask(cls, optimization_setup):
        """check if the binary variable is necessary

        :param optimization_setup: optimization setup object"""
        params = optimization_setup.parameters
        model = optimization_setup.model
        sets = optimization_setup.sets

        mask = xr.DataArray(False, coords=[model.variables.coords["set_time_steps_yearly"],
                                           model.variables.coords["set_technologies"],
                                           model.variables.coords["set_capacity_types"],
                                           model.variables.coords["set_location"], ])

        # used in transport technology
        techs = list(sets["set_transport_technologies"])
        if len(techs) > 0:
            edges = list(sets["set_edges"])
            sub_mask = (params.distance.loc[techs, edges] * params.capex_per_distance_transport.loc[techs, edges] != 0)
            sub_mask = sub_mask.rename({"set_transport_technologies": "set_technologies", "set_edges": "set_location"})
            mask.loc[:, techs, :, edges] |= sub_mask

        # used in constraint_technology_min_capacity_addition
        mask = mask | (params.capacity_addition_min.notnull() & (params.capacity_addition_min != 0))

        # used in constraint_technology_max_capacity_addition
        index_values, index_names = Element.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup)
        index = ZenIndex(index_values, index_names)
        sub_mask = (params.capacity_addition_max.notnull() & (params.capacity_addition_max != np.inf) & (params.capacity_addition_max != 0))
        for tech, capacity_type in index.get_unique([0, 1]):
            locs = index.get_values(locs=[tech, capacity_type], levels=2, unique=True)
            mask.loc[:, tech, capacity_type, locs] |= sub_mask.loc[tech, capacity_type]

        return mask

    @classmethod
    def get_existing_quantity(cls, optimization_setup, type_existing_quantity):
        """
        get existing capacities of all technologies
        :param optimization_setup: The OptimizationSetup the element is part of
        :param type_existing_quantity: capacity or cost_capex
        :return: The existing capacities
        """

        index_values, index_names = Element.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], optimization_setup)
        # get all the capacities
        index_arrs = IndexSet.tuple_to_arr(index_values, index_names)
        coords = [optimization_setup.sets.get_coord(data, name) for data, name in zip(index_arrs, index_names)]
        existing_quantities = xr.DataArray(np.nan, coords=coords, dims=index_names)
        values = np.zeros(len(index_values))
        for i, (tech, capacity_type, loc, time) in enumerate(index_values):
            values[i] = Technology.get_available_existing_quantity(optimization_setup, tech, capacity_type, loc, time,
                                                                   type_existing_quantity=type_existing_quantity)
        existing_quantities.loc[index_arrs] = values
        return existing_quantities


class TechnologyRules(GenericRule):
    """
    Rules for the Technology class
    """

    def __init__(self, optimization_setup):
        """
        Inits the rules
        :param optimization_setup: OptimizationSetup of the element
        """

        super().__init__(optimization_setup)

    # Disjunctive Constraints
    # -----------------------

    def disjunct_on_technology(self, tech, capacity_type, loc, time):
        """definition of disjunct constraints if technology is On
        iterate through all subclasses to find corresponding implementation of disjunct constraints

        :param tech: technology
        :param capacity_type: capacity type
        :param loc: location
        :param time: time step
        """
        for subclass in Technology.__subclasses__():
            if tech in self.optimization_setup.get_all_names_of_elements(subclass):
                # extract the relevant binary variable (not scalar, .loc is necessary)
                binary_var = self.optimization_setup.model.variables["tech_on_var"].loc[tech, capacity_type, loc, time]
                subclass.disjunct_on_technology(self.optimization_setup, tech, capacity_type, loc, time, binary_var)
                return None

    def disjunct_off_technology(self, tech, capacity_type, loc, time):
        """definition of disjunct constraints if technology is off
        iterate through all subclasses to find corresponding implementation of disjunct constraints

        :param tech: technology
        :param capacity_type: capacity type
        :param loc: location
        :param time: time step
        """
        for subclass in Technology.__subclasses__():
            if tech in self.optimization_setup.get_all_names_of_elements(subclass):
                # extract the relevant binary variable (not scalar, .loc is necessary)
                binary_var = self.optimization_setup.model.variables["tech_off_var"].loc[tech, capacity_type, loc, time]
                subclass.disjunct_off_technology(self.optimization_setup, tech, capacity_type, loc, time, binary_var)
                return None

    # Rule-based constraints
    # -----------------------

    def constraint_cost_capex_total(self):
        """ sums over all technologies to calculate total capex

        .. math::
            CAPEX_y = \\sum_{h\\in\mathcal{H}}\\sum_{p\\in\mathcal{P}}A_{h,p,y}+\\sum_{k\\in\mathcal{K}}\\sum_{n\\in\mathcal{N}}A^\mathrm{e}_{k,n,y}

        :return: linopy constraint
        """

        ### index sets
        # skipped because rule-based constraint

        ### masks
        # skipped because rule-based constraint

        ### index loop
        # we loop over all years
        lhs = self.variables["cost_capex_total"] - self.variables["capex_yearly"].sum(["set_technologies","set_capacity_types","set_location"])
        rhs = 0
        constraints = lhs == rhs
        ### return
        self.constraints.add_constraint("constraint_cost_capex_total",constraints)
        # return self.constraints.return_constraints(constraints,self.model,stack_dim_name="set_time_steps_yearly")

    def constraint_cost_opex_total(self):
        """ sums over all technologies to calculate total opex

        .. math::
            OPEX_y = \sum_{h\in\mathcal{H}}\sum_{p\in\mathcal{P}} OPEX_{h,p,y}

        :return: linopy constraint
        """

        ### index sets
        # skipped because rule-based constraint

        ### masks
        # skipped because rule-based constraint

        ### index loop
        # we loop over all years
        # constraints = {}
        # for year in self.energy_system.set_time_steps_yearly:
        #
        #     ### auxiliary calculations
        #     term_sum_yearly = self.variables["opex_yearly"].loc[..., year].sum()
        #
        #     ### formulate constraint
        #     lhs = (self.variables["cost_opex_total"].loc[year]
        #            - term_sum_yearly)
        #     rhs = 0
        #     constraints[year] = lhs == rhs
        lhs = self.variables["cost_opex_total"] - self.variables["opex_yearly"].sum(["set_technologies","set_location"])
        rhs = 0
        constraints = lhs == rhs
        ### return
        self.constraints.add_constraint("constraint_cost_opex_total",constraints)
        # return self.constraints.return_constraints(constraints,self.model,stack_dim_name="set_time_steps_yearly")

    # Block-based constraints
    # -----------------------

    def constraint_technology_capacity_limit(self):
        """limited capacity_limit of technology

        .. math::
            \mathrm{if\ existing\ capacities\ < capacity\ limit}\ s^\mathrm{max}_{h,p,y} \geq S_{h,p,y}
        .. math::
            \mathrm{else}\ \Delta S_{h,p,y} = 0

        :return: linopy constraints
        """

        ### index sets
        # not necessary

        ### masks
        # take the maximum of the capacity limit and the existing capacities.
        # If the capacity limit is 0 (or lower than existing capacities), the maximum is the existing capacity
        maximum_capacity_limit = np.maximum(self.parameters.existing_capacities,self.parameters.capacity_limit)
        # create mask so that skipped if capacity_limit is inf
        m = maximum_capacity_limit != np.inf

        ### index loop
        # not necessary

        ### auxiliary calculations
        # not necessary

        ### formulate constraint
        lhs = self.variables["capacity"].where(m)
        rhs = maximum_capacity_limit.where(m,0.0)
        constraints = lhs <= rhs

        ### return
        self.constraints.add_constraint("constraint_technology_capacity_limit",constraints)

    def constraint_technology_min_capacity_addition(self):
        """ min capacity addition of technology

        .. math::
            s^\mathrm{add, min}_{h} B_{i,p,y} \le \Delta S_{h,p,y}

        :return: linopy constraints
        """

        ### index sets
        index_values, index_names = Element.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], self.optimization_setup)
        index = ZenIndex(index_values, index_names)
        tech_arr, capacity_type_arr = index.get_unique(["set_technologies", "set_capacity_types"], as_array=True)

        ### masks
        # we create a mask here only to avoid having constraints with binary variables when it's not necessary
        # passing constraints with binary variables to gurobi, even of the type 0 * binary_var, means that no
        # dual variables are returned
        mask = xr.zeros_like(self.parameters.capacity_addition_min, dtype=bool)
        mask.loc[tech_arr, capacity_type_arr] = True
        mask &= self.parameters.capacity_addition_min != 0

        ### index loop
        # not necessary

        ### auxiliary calculations
        # if the mask is empty, we don't need to do anything and abort here
        if not mask.any():
            return None

        ### formulate constraint
        lhs = mask * (self.parameters.capacity_addition_min * self.variables["technology_installation"]
                      - self.variables["capacity_addition"])
        rhs = 0
        constraints = lhs <= rhs

        ### return
        self.constraints.add_constraint("constraint_technology_min_capacity_addition",constraints)

    def constraint_technology_max_capacity_addition(self):
        """max capacity addition of technology

        .. math::
            s^\mathrm{add, max}_{h} B_{i,p,y} \ge \Delta S_{h,p,y}

        :return: linopy constraints
        """

        ### index sets
        index_values, index_names = Element.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], self.optimization_setup)
        index = ZenIndex(index_values, index_names)

        ### masks
        # not necessary

        ### index loop
        constraints = {}
        for tech, capacity_type in index.get_unique(["set_technologies", "set_capacity_types"]):
            # not that the else here is just a dummy
            if self.parameters.capacity_addition_max.loc[tech, capacity_type] != np.inf:

                ### auxiliary calculations
                # we only want a constraints with a binary variable if the corresponding max_built_capacity is not zero
                if np.any(self.parameters.capacity_addition_max.loc[tech, capacity_type].notnull() & (self.parameters.capacity_addition_max.loc[tech, capacity_type] != 0)):
                    term_installation = self.parameters.capacity_addition_max.loc[tech, capacity_type].item() * self.variables["technology_installation"].loc[tech, capacity_type]
                else:
                    # dummy
                    term_installation = self.variables["capacity_addition"].loc[tech, capacity_type].where(False)

                ### formulate constraint
                lhs = (- self.variables["capacity_addition"].loc[tech, capacity_type]
                       + term_installation)
                rhs = 0
                constraints[(tech,capacity_type)] = lhs >= rhs

        ### return
        self.constraints.add_constraint("constraint_technology_max_capacity_addition",constraints)
        # return self.constraints.return_constraints(constraints,
        #                                           model=self.model,
        #                                           index_values=index.get_unique(["set_technologies", "set_capacity_types"]),
        #                                           index_names=["set_technologies", "set_capacity_types"])

    def constraint_technology_construction_time(self):
        """ construction time of technology, i.e., time that passes between investment and availability

        .. math::
            \mathrm{if\ start\ time\ step\ in\ set\ time\ steps\ yearly}\ \Delta S_{h,p,y} = S_{h,p,y}^\mathrm{invest}
        .. math::
            \mathrm{elif\ start\ time\ step\ in\ set\ time\ steps\ yearly\ entire\ horizon}\ \Delta S_{h,p,y} = s^\mathrm{invest, exist}_{h,p,y}
        .. math::
            \mathrm{else}\ \Delta S_{h,p,y} = 0

        :return: linopy constraints
        """

        ### index sets
        index_values, index_names = Element.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], self.optimization_setup)
        index = ZenIndex(index_values, index_names)

        ### masks
        # not necessary

        ### index loop
        # we loop over technologies and years, because the conditions depend on the year and the technology
        # we vectorize over capacity types and locations
        # get investment time step
        investment_time = pd.Series(
            {(t, y,Technology.get_investment_time_step(self.optimization_setup, t, y)): 1 for t, y in
             index.get_unique(["set_technologies", "set_time_steps_yearly"])})
        investment_time.index.names = ["set_technologies", "set_time_steps_yearly","set_time_steps_construction"]

        # select masks
        mask_current_time_steps = investment_time.index.get_level_values("set_time_steps_construction").isin(self.sets["set_time_steps_yearly"])
        mask_other_time_steps = investment_time.isin(self.sets["set_time_steps_yearly_entire_horizon"]) & ~mask_current_time_steps
        mask_outside_time_steps = ~(mask_other_time_steps | mask_current_time_steps)
        investment_time = investment_time
        # broadcast capacity investment and capacity investment existing
        capacity_investment = self.variables["capacity_investment"]
        investment_time_current = investment_time[mask_current_time_steps].dropna().to_xarray().broadcast_like(capacity_investment.mask).fillna(0)
        investment_time_other = investment_time[mask_other_time_steps].dropna().to_xarray().broadcast_like(capacity_investment.mask).fillna(0)

        capacity_investment = capacity_investment.rename({"set_time_steps_yearly": "set_time_steps_construction"}).broadcast_like(investment_time_current)
        capacity_investment_existing = self.parameters.capacity_investment_existing
        capacity_investment_existing = capacity_investment_existing.rename({"set_time_steps_yearly_entire_horizon": "set_time_steps_construction"}).broadcast_like(investment_time_other)

        ### formulate constraint
        lhs = lp.merge(
            1 * self.variables["capacity_addition"],
            - (investment_time_current*capacity_investment).sum("set_time_steps_construction")
            , compat="broadcast_equals")
        rhs = (investment_time_other*capacity_investment_existing).sum("set_time_steps_construction")
        rhs = xr.align(lhs.const,rhs,join="left")[1]
        constraints = lhs == rhs

        ### return
        self.constraints.add_constraint("constraint_technology_construction_time",constraints)
        # return self.constraints.return_constraints(constraints,
        #                                           model=self.model,
        #                                           index_values=index.get_unique(["set_technologies", "set_time_steps_yearly"]),
        #                                           index_names=["set_technologies", "set_time_steps_yearly"])

    def constraint_technology_lifetime(self):
        """ limited lifetime of the technologies. calculates 'capacity', i.e., the capacity at the end of the year and
        'capacity_previous', i.e., the capacity at the beginning of the year

        .. math::
            S_{h,p,y} = \\sum_{\\tilde{y}=\\max(y_0,y-\\lceil\\frac{l_h}{\\Delta^\mathrm{y}}\\rceil+1)}^y \\Delta S_{h,p,\\tilde{y}}
            + \\sum_{\\hat{y}=\\psi(\\min(y_0-1,y-\\lceil\\frac{l_h}{\\Delta^\mathrm{y}}\\rceil+1))}^{\\psi(y_0)} \\Delta s^\mathrm{ex}_{h,p,\\hat{y}}

        :return: linopy constraints
        """

        ### index sets
        index_values, index_names = Element.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], self.optimization_setup)
        index = ZenIndex(index_values, index_names)

        ### masks
        lt_range = pd.Series({(t, y): list(Technology.get_lifetime_range(self.optimization_setup, t, y)) for t, y in
                              index.get_unique(["set_technologies", "set_time_steps_yearly"])})
        lt_range = pd.DataFrame(lt_range.to_list(), index=lt_range.index).stack()
        lt_range[:] = -1
        lt_range.index.names = ["set_technologies", "set_time_steps_yearly", "set_time_steps_yearly_prev"]
        lt_range = lt_range.to_xarray().broadcast_like(self.variables["capacity"].mask).fillna(0)
        capacity_addition = self.variables["capacity_addition"].rename({"set_time_steps_yearly": "set_time_steps_yearly_prev"})
        capacity_addition = capacity_addition.broadcast_like(lt_range)
        expr = (lt_range * capacity_addition).sum("set_time_steps_yearly_prev")
        lhs = lp.merge(1 * self.variables["capacity"], expr, compat="broadcast_equals")
        lhs_previous = lp.merge(1 * self.variables["capacity_previous"], expr, 1 * self.variables["capacity_addition"],
                                compat="broadcast_equals")
        rhs = xr.align(lhs.const,self.parameters.existing_capacities,join="left")[1]
        constraints = lhs == rhs
        constraints_previous = lhs_previous == rhs

        ### return
        self.constraints.add_constraint("constraint_technology_lifetime",constraints)
        self.constraints.add_constraint("constraint_technology_lifetime_previous",constraints_previous)

    def constraint_technology_diffusion_limit(self):
        """limited technology diffusion based on the existing capacity in the previous year

        .. math::
                \\Delta S_{j,e,y}\\leq ((1+\\vartheta_j)^{\\Delta^\mathrm{y}}-1)K_{j,e,y}
                +\\Delta^\mathrm{y}(\\xi\\sum_{\\tilde{j}\\in\\tilde{\mathcal{J}}}S_{\\tilde{j},e,y} + \\zeta_j)

        :return: linopy constraints
        """

        ### index sets
        index_values, index_names = Element.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], self.optimization_setup)
        index = ZenIndex(index_values, index_names)

        ### masks
        # not necessary
        capacity_addition = self.variables["capacity_addition"]
        capacity_existing = self.parameters.capacity_existing
        # create mask for knowledge spillover rate (sr) to exclude transport technologies
        mask_technology_type = pd.Series(index=xr.DataArray(self.sets["set_technologies"]), data=1)
        mask_technology_type.index.name = "set_technologies"
        mask_technology_type[mask_technology_type.index.isin(self.sets["set_transport_technologies"])] = 0
        mask_technology_type = mask_technology_type.to_xarray()
        # create mask for knowledge spillover rate (sr) to exclude edges
        mask_location = pd.Series(index=capacity_addition.coords["set_location"], data=1)
        mask_location.index.name = "set_location"
        mask_location[mask_location.index.isin(self.sets["set_edges"])] = 0
        mask_location = mask_location.to_xarray()
        # create xarray for previous years
        knowledge_depreciation_rate = self.system["knowledge_depreciation_rate"]
        interval_between_years = self.system["interval_between_years"]
        years = pd.MultiIndex.from_tuples(
            [(y, py) for y, py in
             itertools.product(self.sets["set_time_steps_yearly"], self.sets["set_time_steps_yearly"])
             if py < y],
            names=["set_time_steps_yearly", "set_time_steps_yearly_prev"])
        # kdr for capacity additions
        kdr = {(y, py): (1 - knowledge_depreciation_rate) ** (interval_between_years * (y - 1 - py))
               for y, py in years}
        kdr = pd.Series(kdr)
        kdr.index.names = ["set_time_steps_yearly", "set_time_steps_yearly_prev"]
        kdr = kdr.to_xarray().fillna(0)

        years = pd.Series(index=years, data=1)
        years = years.to_xarray().fillna(0)
        # expand and sum capacity addition over all nodes for spillover
        capacity_addition_years = capacity_addition.rename({"set_time_steps_yearly": "set_time_steps_yearly_prev"}).broadcast_like(years)
        location_index = pd.Series(index=pd.MultiIndex.from_product(
            [capacity_addition.coords["set_location"].values, capacity_addition.coords["set_location"].values],
            names=["set_location", "set_location_temp"])).to_xarray()
        capacity_addition_location = capacity_addition_years.rename({"set_location": "set_location_temp"}).broadcast_like(
            location_index).sel({"set_location_temp": self.sets["set_nodes"]}).sum("set_location_temp")
        # calculate term spillover
        term_spillover = capacity_addition_location - capacity_addition_years
        sr = xr.full_like(term_spillover.const, self.parameters.knowledge_spillover_rate)
        sr = sr.where(mask_technology_type, 0).where(mask_location, 0)
        # annual knowledge addition
        term_knowledge = capacity_addition_years + sr * term_spillover
        kdr = kdr.broadcast_like(capacity_addition_years.lower)
        # technology diffusion rate per investment period
        tdr = (1 + self.parameters.max_diffusion_rate) ** interval_between_years - 1
        tdr = tdr.broadcast_like(capacity_addition.lower)
        term_knowledge = tdr*(term_knowledge * kdr).sum("set_time_steps_yearly_prev")
        term_knowledge_no_spillover = tdr * (capacity_addition_years * kdr).sum("set_time_steps_yearly_prev")
        # unbounded market share
        capacity_previous = self.variables["capacity_previous"]
        market_share_unbounded = {
            (t,ot): self.parameters.market_share_unbounded if self.sets["set_reference_carriers"][t][0] == self.sets["set_reference_carriers"][ot][0] else 0 for t,ot in itertools.product(self.sets["set_technologies"], self.sets["set_technologies"])
             }
        market_share_unbounded = pd.Series(market_share_unbounded)
        market_share_unbounded.index.names = ["set_technologies", "set_other_technologies"]
        market_share_unbounded = market_share_unbounded.to_xarray().broadcast_like(capacity_previous.lower)
        term_unbounded_addition = (market_share_unbounded * capacity_previous.rename({"set_technologies":"set_other_technologies"})).sum("set_other_technologies")
        # build lhs
        lhs_an = capacity_addition - term_knowledge - term_unbounded_addition
        lhs_sn = (capacity_addition - term_knowledge_no_spillover - term_unbounded_addition).sum("set_location")
        # build rhs
        delta_years = interval_between_years*(capacity_addition.coords["set_time_steps_yearly"]-1-self.energy_system.set_time_steps_yearly[0])
        lifetime_existing = self.parameters.lifetime_existing
        lifetime = self.parameters.lifetime
        kdr_existing = (1 - knowledge_depreciation_rate)**(delta_years + lifetime - lifetime_existing)
        capacity_existing_total = capacity_existing + self.parameters.knowledge_spillover_rate*(capacity_existing.sum("set_location")-capacity_existing)
        capacity_existing_total_nosr = capacity_existing
        rhs_an = tdr * (capacity_existing_total * kdr_existing).sum("set_technologies_existing") + self.parameters.capacity_addition_unbounded
        rhs_sn = (tdr * (capacity_existing_total_nosr * kdr_existing).sum("set_technologies_existing") + self.parameters.capacity_addition_unbounded).sum("set_location")
        # combine
        constraints_sn = lhs_sn <= rhs_sn
        constraints_an = lhs_an <= rhs_an
        # ### index loop
        # # we loop over technologies, capacity types and time steps, to accurately capture the conditions in the constraint
        # # we vectorize over locations
        # constraints_sn = {} # single node
        # constraints_an = {} # all nodes
        # for tech, year in index.get_unique(["set_technologies", "set_time_steps_yearly"]):
        #     # skip if max diffusion rate = inf
        #     if self.parameters.max_diffusion_rate.loc[tech, year] != np.inf:
        #         ### auxiliary calculations
        #         # mask for the capacity types that are not considered
        #         capacity_types = index.get_values([tech, slice(None), slice(None), year], "set_capacity_types", unique=True)
        #         mask = xr.DataArray(np.nan, coords=[self.variables["capacity"].coords["set_capacity_types"]], dims=["set_capacity_types"])
        #         mask.loc[capacity_types] = 1
        #
        #         interval_between_years = self.system["interval_between_years"]
        #         knowledge_depreciation_rate = self.system["knowledge_depreciation_rate"]
        #         reference_carrier = self.sets["set_reference_carriers"][tech][0]
        #         if tech in self.sets["set_transport_technologies"]:
        #             set_locations = self.sets["set_edges"]
        #             set_technology = self.sets["set_transport_technologies"]
        #             knowledge_spillover_rate = 0
        #         else:
        #             set_locations = self.sets["set_nodes"]
        #             knowledge_spillover_rate = self.parameters.knowledge_spillover_rate
        #             if tech in self.sets["set_conversion_technologies"]:
        #                 set_technology = self.sets["set_conversion_technologies"]
        #             else:
        #                 set_technology = self.sets["set_storage_technologies"]
        #
        #         # actual years between first invest year step and end_year
        #         delta_year = interval_between_years * (year - 1 - self.energy_system.set_time_steps_yearly[0])
        #         horizon_year = np.arange(self.energy_system.set_time_steps_yearly[0], year)
        #         horizon_year = self.sets["set_time_steps_yearly"].intersection(horizon_year)
        #         if len(horizon_year) >= 1:
        #             term_total_capacity_knowledge_addition = ((self.variables["capacity_addition"].loc[tech, :, set_locations, horizon_year]  # add spillover from other regions
        #                                                   + knowledge_spillover_rate * (self.variables["capacity_addition"].loc[tech, :, set_locations, horizon_year].sum("set_location") - self.variables["capacity_addition"].loc[tech, :, set_locations, horizon_year]))
        #                                                  * (1 - knowledge_depreciation_rate) ** (interval_between_years * (year - 1 - horizon_year))).sum("set_time_steps_yearly")
        #         else:
        #             # dummy term
        #             term_total_capacity_knowledge_addition = self.variables["capacity_investment"].loc[tech, :, set_locations, year].where(False)
        #
        #         # total capacity in previous year; if year is first year step of interval, use existing capacities of present year
        #         other_techs = [other_tech for other_tech in set_technology if self.sets["set_reference_carriers"][other_tech][0] == reference_carrier]
        #         ### formulate constraint
        #         # build the lhs todo a small delay
        #         lhs_an = (self.variables["capacity_addition"].loc[tech, :, set_locations, year]
        #                - ((1 + self.parameters.max_diffusion_rate.loc[tech, year].item()) ** interval_between_years - 1) * term_total_capacity_knowledge_addition
        #                - self.parameters.market_share_unbounded * self.variables["capacity_previous"].loc[other_techs, :,set_locations, year].sum("set_technologies"))
        #         lhs_an *= mask
        #         # TODo THIS DOES NOT WORK PROPERLY AS IT DOES NOT EXCLUDE THE SPILLOVER
        #         lhs_sn = lhs_an.sum("set_location")
        #         # build the rhs todo a small delay
        #
        #         existing_year = self.sets["set_technologies_existing"][tech]
        #         # Note: instead of summing over all but one location, we sum over all and then subtract one todo outsource
        #         term_total_capacity_knowledge_existing = (
        #                 (self.parameters.capacity_existing.loc[tech, :, set_locations,existing_year]  # add spillover from other regions
        #                 + knowledge_spillover_rate * (
        #                          self.parameters.capacity_existing.loc[tech, :,set_locations, existing_year].sum("set_location")
        #                          - self.parameters.capacity_existing.loc[tech, :, set_locations,existing_year]))
        #                 * (1 - knowledge_depreciation_rate) ** (
        #                         delta_year + self.parameters.lifetime.loc[tech].item() - self.parameters.lifetime_existing.loc[tech, set_locations, existing_year])
        #         ).sum("set_technologies_existing")
        #
        #         rhs_an = ((1 + self.parameters.max_diffusion_rate.loc[tech, year].item()) ** interval_between_years - 1) * term_total_capacity_knowledge_existing + self.parameters.capacity_addition_unbounded.loc[tech]
        #         # add initial market share until which the diffusion rate is unbounded
        #         rhs_an *= mask
        #         rhs_sn = rhs_an.sum("set_location")
        #         # combine
        #         constraints_sn[(tech,year)] = lhs_sn <= rhs_sn
        #         constraints_an[(tech,year)] = lhs_an <= rhs_an

        ### add constraints
        self.constraints.add_constraint("constraint_technology_diffusion_limit",constraints_an)
        self.constraints.add_constraint("constraint_technology_diffusion_limit_total",constraints_sn)
        # return self.constraints.return_constraints(constraints, model=self.model, stack_dim_name="diffusion_limit_dim")
        # cp.disable()
        # cp.print_stats("cumtime")

    def constraint_capex_yearly(self):
        """ aggregates the capex of built capacity and of existing capacity

        .. math::
            A_{h,p,y} = f_h (\\sum_{\\tilde{y} = \\max(y_0,y-\\lceil\\frac{l_h}{\\Delta^\mathrm{y}}\\rceil+1)}^y \\alpha_{h,y}\\Delta S_{h,p,\\tilde{y}}
            + \\sum_{\\hat{y}=\\psi(\\min(y_0-1,y-\\lceil\\frac{l_h}{\\Delta^\mathrm{y}}\\rceil+1))}^{\\psi(y_0)} \\alpha_{h,y_0}\\Delta s^\mathrm{ex}_{h,p,\\hat{y}})

        :return: linopy constraints
        """

        ### index sets
        index_values, index_names = Element.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_yearly"], self.optimization_setup)
        index = ZenIndex(index_values, index_names)

        ### masks
        # not necessary

        dr = self.parameters.discount_rate
        lt = self.parameters.lifetime
        if dr != 0:
            a = ((1 + dr) ** lt * dr) / ((1 + dr) ** lt - 1)
        else:
            a = 1 / lt
        lt_range = pd.Series({(t, y): list(Technology.get_lifetime_range(self.optimization_setup, t, y)) for t, y in
                              index.get_unique(["set_technologies", "set_time_steps_yearly"])})
        lt_range = pd.DataFrame(lt_range.to_list(), index=lt_range.index).stack()
        lt_range[:] = -1
        lt_range.index.names = ["set_technologies", "set_time_steps_yearly", "set_time_steps_yearly_prev"]
        lt_range = lt_range.to_xarray().broadcast_like(self.variables["capacity"].mask).fillna(0)

        cost_capex = self.variables["cost_capex"].rename(
            {"set_time_steps_yearly": "set_time_steps_yearly_prev"})
        cost_capex = cost_capex.broadcast_like(lt_range)
        expr = (lt_range * a * cost_capex).sum("set_time_steps_yearly_prev")
        lhs = lp.merge(1 * self.variables["capex_yearly"], expr, compat="broadcast_equals")
        rhs = a * self.parameters.existing_capex
        constraints = lhs == rhs

        ### return
        self.constraints.add_constraint("constraint_capex_yearly",constraints)

    def constraint_opex_technology(self):
        """ calculate opex of each technology

        .. math::
            \mathrm{if\ tech\ is\ conversion\ tech}\ OPEX_{h,p,t}^\mathrm{cost} = \\beta_{h,p,t} G_{i,n,t,y}^\mathrm{r}
        .. math::
            \mathrm{if\ tech\ is\ transport\ tech}\ OPEX_{h,p,t}^\mathrm{cost} = \\beta_{h,p,t} F_{j,e,t}
        .. math::
            \mathrm{if\ tech\ is\ storage\ tech}\ OPEX_{h,p,t}^\mathrm{cost} = \\beta_{h,p,t} (\\underline{H}_{k,n,t} + \\overline{H}_{k,n,t})

        :return: linopy constraints
        """

        ### index sets
        index_values, index_names = Element.create_custom_set(["set_technologies", "set_location", "set_time_steps_operation"], self.optimization_setup)
        index = ZenIndex(index_values, index_names)

        ### masks
        # not necessary

        ### index loop
        # we loop over all technologies because of the reference carrier and flow which depend on the technology
        # we vectorize over locations and time steps
        constraints = {}
        for tech in index.get_unique(["set_technologies"]):
            locs = index.get_values([tech], "set_location", unique=True)
            reference_carrier = self.sets["set_reference_carriers"][tech][0]
            if tech in self.sets["set_conversion_technologies"]:
                if reference_carrier in self.sets["set_input_carriers"][tech]:
                    reference_flow = self.variables["flow_conversion_input"].loc[tech, reference_carrier, locs].to_linexpr()
                    reference_flow = reference_flow.rename({"set_nodes": "set_location"})
                else:
                    reference_flow = self.variables["flow_conversion_output"].loc[tech, reference_carrier, locs].to_linexpr()
                    reference_flow = reference_flow.rename({"set_nodes": "set_location"})
            elif tech in self.sets["set_transport_technologies"]:
                reference_flow = self.variables["flow_transport"].loc[tech, locs].to_linexpr()
                reference_flow = reference_flow.rename({"set_edges": "set_location"})
            else:
                reference_flow = self.variables["flow_storage_charge"].loc[tech, locs] + self.variables["flow_storage_discharge"].loc[tech, locs]
                reference_flow = reference_flow.rename({"set_nodes": "set_location"})

            term_reference_flow = - self.parameters.opex_specific_variable.loc[tech, locs] * reference_flow

            ### formulate constraint
            # the first term is just to ensure full shape
            lhs = lp.merge(self.variables["cost_opex"].loc[tech].where(False).to_linexpr(),
                           self.variables["cost_opex"].loc[tech, locs].to_linexpr(),
                           term_reference_flow,
                           compat="broadcast_equals")
            rhs = 0
            constraints[tech] = lhs == rhs

        ### return
        self.constraints.add_constraint("constraint_opex_technology",constraints)
        # return self.constraints.return_constraints(constraints,
        #                                           model=self.model,
        #                                           index_values=index.get_unique(["set_technologies"]),
        #                                           index_names=["set_technologies"])

    def constraint_opex_yearly(self):
        """ yearly opex for a technology at a location in each year

        .. math::
            OPEX_{h,p,y} = \sum_{t\in\mathcal{T}}\tau_t OPEX_{h,p,t}^\mathrm{cost}
            + \gamma_{h,y} S_{h,p,y}
            #TODO complete constraint (second summation symbol)

        :return: linopy constraints
        """

        ### index sets

        ### masks
        # not necessary

        ### index loop
        # we loop over all technologies and yearly time steps because some auxillary calculations depend on the technology
        # we vectorize over locations
        times = {y: self.parameters.time_steps_operation_duration.loc[
            self.time_steps.get_time_steps_year2operation(y)].to_series() for y in self.sets["set_time_steps_yearly"]}
        times = pd.concat(times, keys=times.keys())
        times.index.names = ["set_time_steps_yearly", "set_time_steps_operation"]
        times = times.to_xarray().broadcast_like(self.variables["cost_opex"].mask)
        term_opex_variable = (self.variables["cost_opex"] * times).sum("set_time_steps_operation")
        term_opex_fixed = (self.parameters.opex_specific_fixed * self.variables["capacity"]).sum("set_capacity_types")
        lhs = self.variables["opex_yearly"] - term_opex_variable - term_opex_fixed
        rhs = 0
        constraints = lhs == rhs

        # constraints = {}
        # for tech, year in index.get_unique(["set_technologies", "set_time_steps_yearly"]):
        #
        #     ### auxiliary calculations
        #     times = self.time_steps.get_time_steps_year2operation(year)
        #
        #     term_neg_summed_cost_opex = - (self.variables["cost_opex"].loc[tech, :, times] * self.parameters.time_steps_operation_duration.loc[times]).sum(["set_time_steps_operation"])
        #     term_neg_summed_capacities = - lp_sum([self.parameters.opex_specific_fixed.loc[tech, capacity_type, :, year]*self.variables["capacity"].loc[tech, capacity_type, :, year]
        #                                            for capacity_type in self.system["set_capacity_types"] if tech in self.sets["set_storage_technologies"] or capacity_type == self.system["set_capacity_types"][0]])
        #
        #     ### formulate constraint
        #     lhs = lp_sum([1.0 * self.variables["opex_yearly"].loc[tech, :, year],
        #                   term_neg_summed_cost_opex,
        #                   term_neg_summed_capacities])
        #     rhs = 0
        #     constraints[(tech,year)] = lhs == rhs

        ### return
        self.constraints.add_constraint("constraint_opex_yearly",constraints)
        # return self.constraints.return_constraints(constraints,
        #                                           model=self.model,
        #                                           index_values=index.get_unique(["set_technologies", "set_time_steps_yearly"]),
        #                                           index_names=["set_technologies", "set_time_steps_yearly"])

    def constraint_carbon_emissions_technology(self):
        """ calculate carbon emissions of each technology

        .. math::
            \mathrm{if\ tech\ is\ conversion\ tech}\ E_{h,p,t} = \\epsilon_h G_{i,n,t,y}^\mathrm{r}
        .. math::
            \mathrm{if\ tech\ is\ transport\ tech}\ E_{h,p,t} = \\epsilon_h F_{j,e,t}
        .. math::
            \mathrm{if\ tech\ is\ storage\ tech}\ E_{h,p,t} = \\epsilon_h (\\underline{H}_{k,n,t} + \\overline{H}_{k,n,t})

        :return: linopy constraints
        """

        ### index sets
        index_values, index_names = Element.create_custom_set(["set_technologies", "set_location", "set_time_steps_operation"], self.optimization_setup)
        index = ZenIndex(index_values, index_names)

        ### masks
        # not necessary

        ### index loop
        # we loop over all technologies because of the reference carrier and flow which depend on the technology
        # we vectorize over locations and time steps
        constraints = {}
        for tech in index.get_unique(["set_technologies"]):

            ### auxiliary calculations
            locs = index.get_values([tech], 1, unique=True)
            reference_carrier = self.sets["set_reference_carriers"][tech][0]
            if tech in self.sets["set_conversion_technologies"]:
                if reference_carrier in self.sets["set_input_carriers"][tech]:
                    reference_flow = self.variables["flow_conversion_input"].loc[tech, reference_carrier, locs].to_linexpr()
                    reference_flow = reference_flow.rename({"set_nodes": "set_location"})
                else:
                    reference_flow = self.variables["flow_conversion_output"].loc[tech, reference_carrier, locs].to_linexpr()
                    reference_flow = reference_flow.rename({"set_nodes": "set_location"})
            elif tech in self.sets["set_transport_technologies"]:
                reference_flow = self.variables["flow_transport"].loc[tech, locs].to_linexpr()
                reference_flow = reference_flow.rename({"set_edges": "set_location"})
            else:
                reference_flow = self.variables["flow_storage_charge"].loc[tech, locs] + self.variables["flow_storage_discharge"].loc[tech, locs]
                reference_flow = reference_flow.rename({"set_nodes": "set_location"})

            term_reference_flow = - self.parameters.carbon_intensity_technology.loc[tech, locs] * reference_flow

            ### formulate constraint
            # the first term is just to ensure full shape
            lhs = lp.merge(self.variables["carbon_emissions_technology"].loc[tech].where(False).to_linexpr(),
                           self.variables["carbon_emissions_technology"].loc[tech, locs].to_linexpr(),
                           term_reference_flow,
                           compat="broadcast_equals")
            rhs = 0
            constraints[tech] = lhs == rhs

        ### return
        self.constraints.add_constraint("constraint_carbon_emissions_technology",constraints)
        # return self.constraints.return_constraints(constraints,
        #                                           model=self.model,
        #                                           index_values=index.get_unique(["set_technologies"]),
        #                                           index_names=["set_technologies"])

    def constraint_carbon_emissions_technology_total(self):
        """ calculate total carbon emissions of each technology

        .. math::
            E_y^{\mathcal{H}} = \sum_{t\in\mathcal{T}}\sum_{h\in\mathcal{H}} E_{h,p,t} \\tau_{t}

        :return: linopy constraints
        """

        ### index sets
        years = self.energy_system.set_time_steps_yearly
        # this index is just for the sums in the auxiliary calculations
        index_values, index_names = Element.create_custom_set(["set_technologies", "set_location"], self.optimization_setup)
        index = ZenIndex(index_values, index_names)

        ### masks
        # not necessary

        ### index loop
        # we cycle over the years, because the sum of the operational time steps depends on the year
        constraints = {}
        for year in years:

            ### auxiliary calculations
            term_summed_carbon_emissions_technology = []
            for tech in index.get_unique(["set_technologies"]):
                locs = index.get_values([tech], "set_location", unique=True)
                times = self.time_steps.get_time_steps_year2operation(year)
                term_summed_carbon_emissions_technology.append((self.variables["carbon_emissions_technology"].loc[tech, locs, times] * self.parameters.time_steps_operation_duration.loc[times]).sum())
            term_summed_carbon_emissions_technology = lp_sum(term_summed_carbon_emissions_technology)

            ### formulate constraint
            lhs = self.variables["carbon_emissions_technology_total"].loc[year] - term_summed_carbon_emissions_technology
            rhs = 0
            constraints[year] = lhs == rhs

        ### return
        self.constraints.add_constraint("constraint_carbon_emissions_technology_total",constraints)
        # return self.constraints.return_constraints(constraints,
        #                                           model=self.model,
        #                                           index_values=years,
        #                                           index_names=["set_time_steps_yearly"])

    # def constraint_capacity_factor(self):
    #     """ Load is limited by the installed capacity and the maximum load factor
    #
    #     .. math::
    #         \mathrm{if\ tech\ is\ conversion\ tech}\ G_{i,n,t,y}^\mathrm{r} \\leq m_{i,n,t,y}S_{i,n,y}
    #     .. math::
    #         \mathrm{if\ tech\ is\ transport\ tech}\ F_{j,e,t,y}^\mathrm{r} \\leq m_{j,e,t,y}S_{j,e,y}
    #     .. math::
    #         \mathrm{if\ tech\ is\ storage\ tech}\ \\underline{H}_{k,n,t,y}+\\overline{H}_{k,n,t,y}\\leq m_{k,n,t,y}S_{k,n,y}
    #
    #     :return: linopy constraints
    #     """
    #     # TODO split into tech classes
    #     ### index sets
    #     index_values, index_names = Element.create_custom_set(["set_technologies", "set_capacity_types", "set_location", "set_time_steps_operation"], self.optimization_setup)
    #     index = ZenIndex(index_values, index_names)
    #
    #     ### masks
    #     # not necessary
    #
    #     ### index loop
    #     # we oop over all technologies for the conditions and vectorize over the rest
    #     constraints = {}
    #     for tech in index.get_unique(["set_technologies"]):
    #
    #         ### auxiliary calculations
    #         capacity_types, locs, times = index.get_values([tech], [1, 2, 3], unique=True)
    #         # to actual coords to avoid renaming
    #         capacity_types = self.variables.coords["set_capacity_types"].loc[capacity_types]
    #         locs = self.variables.coords["set_location"].loc[locs]
    #         times = self.variables.coords["set_time_steps_operation"].loc[times]
    #         # the reference carrier
    #         reference_carrier = self.sets["set_reference_carriers"][tech][0]
    #         # get invest time step
    #         time_step_year = xr.DataArray([self.optimization_setup.energy_system.time_steps.convert_time_step_operation2year(t) for t in times.data], coords=[times])
    #         # we create the capacity term (the dimension reassignment does not change the variables, just the broadcasting)
    #         term_capacity = self.parameters.max_load.loc[tech, capacity_types, locs, times] * self.variables["capacity"].loc[tech, capacity_types, locs, time_step_year].to_linexpr()
    #
    #         # this term is just to ensure full shape
    #         full_shape_term = self.variables["capacity"].loc[tech, ..., time_step_year].where(False).to_linexpr()
    #
    #         # conversion technology
    #         if tech in self.sets["set_conversion_technologies"]:
    #             if reference_carrier in self.sets["set_input_carriers"][tech]:
    #                 term_flow = -1.0 * self.variables["flow_conversion_input"].loc[tech, reference_carrier, locs, times]
    #             else:
    #                 term_flow = -1.0 * self.variables["flow_conversion_output"].loc[tech, reference_carrier, locs, times]
    #         # transport technology
    #         elif tech in self.sets["set_transport_technologies"]:
    #             term_flow = -1.0 * self.variables["flow_transport"].loc[tech, locs, times]
    #         # storage technology
    #         elif tech in self.sets["set_storage_technologies"]:
    #             system = self.optimization_setup.system
    #             # if limit power
    #             mask = (capacity_types == system["set_capacity_types"][0]).astype(float)
    #             # where true
    #             term_flow = mask*(-1.0 * self.variables["flow_storage_charge"].loc[tech, locs, times] - 1.0 * self.variables["flow_storage_discharge"].loc[tech, locs, times])
    #
    #             # TODO integrate level storage here as well
    #
    #         ### formulate constraint
    #         lhs = lp.merge(lp.merge(term_capacity, term_flow), full_shape_term)
    #         rhs = 0
    #         constraints[tech] = lhs >= rhs
    #
    #     ### return
    #     self.constraints.add_constraint("constraint_capacity_factor",constraints)
    #     # return self.constraints.return_constraints(constraints,
    #     #                                           model=self.model,
    #     #                                           index_values=index.get_unique(["set_technologies"]),
    #     #                                           index_names=["set_technologies"])
