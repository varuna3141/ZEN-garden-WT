"""===========================================================================================================================================================================
Title:          ZEN-GARDEN
Created:        October-2021
Authors:        Alissa Ganter (aganter@ethz.ch)
                Jacob Mannhardt (jmannhardt@ethz.ch)
Organization:   Laboratory of Risk and Reliability Engineering, ETH Zurich

Description:    Class defining the parameters, variables and constraints that hold for all transport technologies.
                The class takes the abstract optimization model as an input, and returns the parameters, variables and
                constraints that hold for the transport technologies.
==========================================================================================================================================================================="""
import logging
import warnings
import pyomo.environ as pe
import numpy as np
from model.objects.technology.technology import Technology
from model.objects.energy_system import EnergySystem

class TransportTechnology(Technology):
    # set label
    label           = "setTransportTechnologies"
    locationType    = "setEdges"
    # empty list of elements
    listOfElements = []
    # dict of reversed edges
    dictReversedEdges = {}

    def __init__(self, tech):
        """init transport technology object
        :param tech: name of added technology"""

        logging.info(f'Initialize transport technology {tech}')
        super().__init__(tech)
        # store input data
        self.storeInputData()
        # add TransportTechnology to list
        TransportTechnology.addElement(self)

    def storeInputData(self):
        """ retrieves and stores input data for element as attributes. Each Child class overwrites method to store different attributes """   
        # get attributes from class <Technology>
        super().storeInputData()
        # set attributes for parameters of child class <TransportTechnology>
        self.distance                       = self.dataInput.extractInputData("distanceEuclidean",indexSets=["setEdges"])
        self.lossFlow                       = self.dataInput.extractAttributeData("lossFlow")["value"]
        # In case you want to have separate capex for capacity and distance
        if EnergySystem.system['DoubleCapexTransport']:
            # both capex terms must be specified
            self.capexSpecific = self.dataInput.extractInputData("capexSpecific",
                                                                 indexSets=["setEdges", "setTimeSteps"],
                                                                 timeSteps=self.setTimeStepsInvest)
            self.capexPerDistance = self.dataInput.extractInputData("capexPerDistance",
                                                                    indexSets=["setEdges", "setTimeSteps"],
                                                                    timeSteps=self.setTimeStepsInvest)
        else:  # Here only capexSpecific is used, and capexPerDistance is set to Zero.
            if self.dataInput.ifAttributeExists("capexPerDistance"):
                self.capexPerDistance   = self.dataInput.extractInputData("capexPerDistance",indexSets=["setEdges","setTimeSteps"],timeSteps= self.setTimeStepsInvest)
                self.capexSpecific      = self.capexPerDistance * self.distance
                self.fixedOpexSpecific  = self.fixedOpexSpecific * self.distance
            elif self.dataInput.ifAttributeExists("capexSpecific"):
                self.capexSpecific  = self.dataInput.extractInputData("capexSpecific",indexSets=["setEdges","setTimeSteps"],timeSteps= self.setTimeStepsInvest)
            else:
                raise AttributeError(f"The transport technology {self.name} has neither capexPerDistance nor capexSpecific attribute.")
            self.capexPerDistance   = self.capexSpecific * 0.0

        # annualize capex
        self.convertToAnnualizedCapex()
        # calculate capex of existing capacity
        self.capexExistingCapacity          = self.calculateCapexOfExistingCapacities()
        # check that existing capacities are equal in both directions if technology is bidirectional
        if self.name in EnergySystem.getSystem()["setBidirectionalTransportTechnologies"]:
            self.checkIfBidirectional()

    def convertToAnnualizedCapex(self):
        """ this method converts the total capex to annualized capex """
        fractionalAnnuity       = self.calculateFractionalAnnuity()
        # annualize capex
        self.capexSpecific      = self.capexSpecific*fractionalAnnuity + self.fixedOpexSpecific
        self.capexPerDistance   = self.capexPerDistance * fractionalAnnuity

    def calculateCapexOfSingleCapacity(self,capacity,index):
        """ this method calculates the annualized capex of a single existing capacity. """
        return self.capexSpecific[index[0]].iloc[0] * capacity

    def checkIfBidirectional(self):
        """ checks that the existing capacities in both directions of bidirectional capacities are equal """
        energySystem = EnergySystem.getEnergySystem()
        for edge in energySystem.setEdges:
            reversedEdge = EnergySystem.calculateReversedEdge(edge)
            TransportTechnology.setReversedEdge(edge=edge, reversedEdge=reversedEdge)
            _existingCapacityEdge = self.existingCapacity[edge]
            _existingCapacityReversedEdge = self.existingCapacity[reversedEdge]
            assert (_existingCapacityEdge == _existingCapacityReversedEdge).all(), \
                f"The existing capacities of the bidirectional transport technology {self.name} are not equal on the edge pair {edge} and {reversedEdge} ({_existingCapacityEdge.to_dict()} and {_existingCapacityReversedEdge.to_dict()})"

    ### --- getter/setter classmethods
    @classmethod
    def setReversedEdge(cls,edge,reversedEdge):
        """ maps the reversed edge to an edge """
        cls.dictReversedEdges[edge] = reversedEdge

    @classmethod
    def getReversedEdge(cls, edge):
        """ get the reversed edge corresponding to an edge """
        return cls.dictReversedEdges[edge]

    ### --- classmethods to construct sets, parameters, variables, and constraints, that correspond to TransportTechnology --- ###
    @classmethod
    def constructSets(cls):
        """ constructs the pe.Sets of the class <TransportTechnology> """
        pass

    @classmethod
    def constructParams(cls):
        """ constructs the pe.Params of the class <TransportTechnology> """
        model = EnergySystem.getConcreteModel()
        
        # distance between nodes
        model.distance = pe.Param(
            cls.createCustomSet(["setTransportTechnologies","setEdges"]),
            initialize = EnergySystem.initializeComponent(cls,"distance"),
            doc = 'distance between two nodes for transport technologies. Dimensions: setTransportTechnologies, setEdges')
        # cost per capacity
        model.capexSpecificTransport = pe.Param(
             cls.createCustomSet(["setTransportTechnologies","setEdges","setTimeStepsInvest"]),
             initialize = EnergySystem.initializeComponent(cls,"capexSpecific",indexNames=["setTransportTechnologies","setEdges","setTimeStepsInvest"]),
             doc = 'capex per unit for transport technologies. Dimensions: setTransportTechnologies, setEdges, setTimeStepsInvest')
        # cost per distance
        model.capexPerDistance = pe.Param(
            cls.createCustomSet(['setTransportTechnologies', 'setEdges', 'setTimeStepsInvest']),
            initialize=EnergySystem.initializeComponent(cls, 'capexPerDistance', indexNames=['setTransportTechnologies', "setEdges", "setTimeStepsInvest"]),
            doc='capex per distance for transport technologies. Dimensions: setTransportTechnologies, setEdges, setTimeStepsInvest')
        # carrier losses
        model.lossFlow = pe.Param(
            model.setTransportTechnologies,
            initialize = EnergySystem.initializeComponent(cls,"lossFlow"),
            doc = 'carrier losses due to transport with transport technologies. Dimensions: setTransportTechnologies')

    @classmethod
    def constructVars(cls):
        """ constructs the pe.Vars of the class <TransportTechnology> """
        def carrierFlowBounds(model,tech ,edge,time):
            """ return bounds of carrierFlow for bigM expression 
            :param model: pe.ConcreteModel
            :param tech: tech index
            :param edge: edge index
            :param time: time index
            :return bounds: bounds of carrierFlow"""
            # convert operationTimeStep to investTimeStep: operationTimeStep -> baseTimeStep -> investTimeStep
            investTimeStep = EnergySystem.convertTimeStepOperation2Invest(tech,time)
            bounds = model.capacity[tech,"power",edge,investTimeStep].bounds
            return(bounds)

        model = EnergySystem.getConcreteModel()
        # flow of carrier on edge
        model.carrierFlow = pe.Var(
            cls.createCustomSet(["setTransportTechnologies","setEdges","setTimeStepsOperation"]),
            domain = pe.NonNegativeReals,
            bounds = carrierFlowBounds,
            doc = 'carrier flow through transport technology on edge i and time t. Dimensions: setTransportTechnologies, setEdges, setTimeStepsOperation. Domain: NonNegativeReals'
        )
        # loss of carrier on edge
        model.carrierLoss = pe.Var(
            cls.createCustomSet(["setTransportTechnologies","setEdges","setTimeStepsOperation"]),
            domain = pe.NonNegativeReals,
            doc = 'carrier flow through transport technology on edge i and time t. Dimensions: setTransportTechnologies, setEdges, setTimeStepsOperation. Domain: NonNegativeReals'
        )
        
    @classmethod
    def constructConstraints(cls):
        """ constructs the pe.Constraints of the class <TransportTechnology> """
        model   = EnergySystem.getConcreteModel()
        system  = EnergySystem.getSystem()
        # Carrier Flow Losses 
        model.constraintTransportTechnologyLossesFlow = pe.Constraint(
            cls.createCustomSet(["setTransportTechnologies","setEdges","setTimeStepsOperation"]),
            rule = constraintTransportTechnologyLossesFlowRule,
            doc = 'Carrier loss due to transport with through transport technology. Dimensions: setTransportTechnologies, setEdges, setTimeStepsOperation'
        ) 
        # capex of transport technologies
        model.constraintCapexTransportTechnology = pe.Constraint(
            cls.createCustomSet(["setTransportTechnologies","setEdges","setTimeStepsInvest"]),
            rule = constraintCapexTransportTechnologyRule,
            doc = 'Capital expenditures for installing transport technology. Dimensions: setTransportTechnologies, setEdges, setTimeStepsInvest'
        )
        # bidirectional transport technologies: capacity on edge must be equal in both directions
        model.constraintBidirectionalTransportTechnology = pe.Constraint(
            cls.createCustomSet(["setTransportTechnologies", "setEdges", "setTimeStepsInvest"]),
            rule=constraintBidirectionalTransportTechnologyRule,
            doc='Forces that transport technology capacity must be equal in both direction. Dimensions: setTransportTechnologies, setEdges, setTimeStepsInvest'
        )

    # defines disjuncts if technology on/off
    @classmethod
    def disjunctOnTechnologyRule(cls,disjunct, tech, edge, time):
        """definition of disjunct constraints if technology is on"""
        model = disjunct.model()
        # get invest time step
        investTimeStep = EnergySystem.convertTimeStepOperation2Invest(tech,time)
        # disjunct constraints min load
        disjunct.constraintMinLoad = pe.Constraint(
            expr=model.carrierFlow[tech, edge, time] >= model.minLoad[tech,edge,time] * model.capacity[tech,edge, investTimeStep]
        )

    @classmethod
    def disjunctOffTechnologyRule(cls,disjunct, tech, edge, time):
        """definition of disjunct constraints if technology is off"""
        model = disjunct.model()
        disjunct.constraintNoLoad = pe.Constraint(
            expr=model.carrierFlow[tech, edge, time] == 0
        )

### --- functions with constraint rules --- ###
def constraintTransportTechnologyLossesFlowRule(model, tech, edge, time):
    """compute the flow losses for a carrier through a transport technology"""
    return(model.carrierLoss[tech, edge, time] ==
           model.distance[tech,edge] * model.lossFlow[tech] * model.carrierFlow[tech, edge, time])

def constraintCapexTransportTechnologyRule(model, tech, edge, time):
    """ definition of the capital expenditures for the transport technology"""
    return (model.capex[tech,"power",edge, time] ==
            model.builtCapacity[tech,"power",edge, time] * model.capexSpecificTransport[tech,edge, time] +
            model.installTechnology[tech,"power", edge, time] * model.distance[tech, edge] * model.capexPerDistance[tech, edge, time])

def constraintBidirectionalTransportTechnologyRule(model, tech, edge, time):
    """ Forces that transport technology capacity must be equal in both direction"""
    system = EnergySystem.getSystem()
    if tech in system["setBidirectionalTransportTechnologies"]:
        reversedEdge = TransportTechnology.getReversedEdge(edge)
        return (model.builtCapacity[tech,"power",edge, time] ==
                model.builtCapacity[tech,"power",reversedEdge, time])
    else:
        return pe.Constraint.Skip
