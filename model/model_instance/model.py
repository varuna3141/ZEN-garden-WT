"""===========================================================================================================================================================================
Title:        ENERGY-CARBON OPTIMIZATION PLATFORM
Created:      October-2021
Authors:      Alissa Ganter (aganter@ethz.ch)
Organization: Laboratory of Risk and Reliability Engineering, ETH Zurich

Description:  Class defining the abstract optimization model.
              The class takes as inputs the properties of the optimization problem. The properties are saved in the
              dictionaries analysis and system which are passed to the class. After initializing the abstract model, the
              class adds carriers and technologies to the abstract model and returns the abstract optimization model.
              The class also includes a method to solve the optimization problem.
==========================================================================================================================================================================="""
import logging
import pyomo.environ as pe
from pyomo.opt import SolverStatus, TerminationCondition
from model.model_instance.objects.carrier import Carrier
from model.model_instance.objects.technology.production_technology import ProductionTechnology
from model.model_instance.objects.technology.transport_technology import TransportTechnology
from model.model_instance.objects.objective_function import ObjectiveFunction
from model.model_instance.objects.mass_balance import MassBalance

class Model:

    def __init__(self, analysis, system):
        """
        create Pyomo Abstract Model
        :param analysis: dictionary defining the analysis framework
        :param system: dictionary defining the system
        """
        self.analysis = analysis
        self.system = system

        self.model = pe.AbstractModel()
        self.addSets()
        self.addElements()
        self.addObjectiveFunction()
        self.addMassBalance()

    def addSets(self):
        """
        This method sets up the sets of the optimization problem.
        Some sets are initialized with default values, if the value is not specified by the input data
        Sets in Pyomo:
            (1) model.ct = Set(model.t) : model.ct is “dictionary” of sets, i.e., model.ct[i] = Set() for all i in model.t
            (2) model.ct = Set(within=model.t) : model.ct is a subset of model.t, Pyomo will do the verification of this
            (3) model.i = Set(initialize=model.t) : makes a copy of whatever is in model.t during the time of construction
            (4) model.i = SetOf(model.t) : references whatever is in model.t at runtime (alias)
        """
        
        # Sets:
        # 'setCarriers' includes the subsets 'setInputCarriers', 'setOutputCarriers'
        # 'setTechnologies' includes the subsets 'setTransportTechnologies', 'setProductionTechnologies', 'setStorageTechnologies'
        
        sets = {'setCarriers':      'Set of carriers',
                'setTechnologies':  'Set of technologies',      
                'setTimeSteps':     'Set of time-steps',
                'setNodes':         'Set of nodes'
                }

        for setName, setProperty in sets.items():
            peSet = pe.Set(doc = setProperty)
            setattr(self.model, setName, peSet)

    def addElements(self):
        """
        This method sets up the parameters, variables and constraints of the carriers of the optimization problem.
        :param analysis: dictionary defining the analysis framework
        :param system: dictionary defining the system
        """
        # TODO create list of carrier types, only add relevant types
        # TODO to create list of carrier tpyes (e.g. general, CO2,...) write a function getCarrierTypes

        # add carrier parameters, variables, and constraints
        Carrier(self)
        # add technology parameters, variables, and constraints
        if self.system['setProductionTechnologies']:
            ProductionTechnology(self)
        if self.system['setTransportTechnologies']:
            TransportTechnology(self)
        if self.system['setStorageTechnologies']:
            print("Storage Technologies are not yet implemented")

        #TODO: decide if mass balance should be added here instead of wihtin Carrier??

    def addObjectiveFunction(self):
        """
        Add objective function to abstract optimization model
        """

        ObjectiveFunction(self)

    def addMassBalance(self):
        """
        Add objective function to abstract optimization model
        """

        MassBalance(self)

    def solve(self, solver, pyoDict):
        """
        Create model instance by assigning parameter values and instantiating the sets
        :param data: dictionary containing the input data
        :return:
        """

        solverName = solver['name']
        # del solver['name']
        solverOptions = solver.copy()
        solverOptions.pop('name')
        
        logging.info("Create model instance")
        # try:
        # import sys
        # sys.exit()
        pyoDict[None]['converEfficiency'] = {('electrolysis','electricity', 'hydrogen'): 0.65,
                                             ('electrolysis','dry_biomass', 'hydrogen'): 0.65,}
        self.instance = self.model.create_instance(data=pyoDict)
        # except:
        #     raise ValueError("Please provide pyoDict with input data.")

        logging.info(f"Solve model instance using {solverName}")
        self.opt = pe.SolverFactory(solverName, options=solverOptions)
        self.results = self.opt.solve(self.instance, tee=True)
        
        # TODO save and evaluate results

