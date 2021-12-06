"""===========================================================================================================================================================================
Title:        ENERGY-CARBON OPTIMIZATION PLATFORM
Created:      November-2021
Authors:      Alissa Ganter (aganter@ethz.ch)
Organization: Laboratory of Risk and Reliability Engineering, ETH Zurich

Description:  Class containing the mass balance and its attributes.
==========================================================================================================================================================================="""
from model.model_instance.objects.element import Element
import pyomo.environ as pe

class MassBalance(Element):

    def __init__(self, object):
        """initialization of the mass balance
        :param object: object of the abstract optimization model """

        super().__init__(object)
        constraint = {'constraintNodalMassBalance':    'nodal mass balance for each time step. \
                                                        \n\t Dimensions: setCarriers, setNodes, setTimeSteps'}
        self.addConstr(constraint)

    # RULES
    @staticmethod
    def constraintNodalMassBalanceRule(model, carrier, node, time):
        """" nodal mass balance for each time step.
        \n\t Dimensions: setCarriers, setNodes, setTimeSteps"""
        carrierImport, carrierExport = 0, 0
        if carrier in model.setInputCarriers:
                carrierImport = model.importCarrier[carrier, node, time]

        demand = 0
        if carrier in model.setOutputCarriers:
            demand = model.demandCarrier[carrier, node, time]
            carrierExport = model.exportCarrier[carrier, node, time]

        carrierProductionIn, carrierProductionOut = 0, 0
        if hasattr(model, 'setProductionTechnologies'):
            if carrier in model.setInputCarriers:
                carrierProductionIn = sum(model.inputProductionTechnologies[carrier, tech, node, time] for tech in
                                          model.setProductionTechnologies)
            if carrier in model.setOutputCarriers:
                carrierProductionOut = sum(-model.outputProductionTechnologies[carrier, tech, node, time] for tech in
                                           model.setProductionTechnologies)

        carrierFlowIn, carrierFlowOut = 0, 0
        if hasattr(model, 'setTransportTechnologies') and carrier in model.setTransportCarriers:
            carrierFlowIn = sum(
                sum(model.carrierFlow[carrier, tech, aliasNode, node, time] for aliasNode in model.setAliasNodes) for
                tech in model.setTransportTechnologies)
            carrierFlowOut = sum(
                sum(model.carrierFlow[carrier, tech, node, aliasNode, time] for aliasNode in model.setAliasNodes) for
                tech in model.setTransportTechnologies)

        # TODO implement storage

        return (carrierImport - carrierExport
                + carrierProductionIn - carrierProductionOut
                + carrierFlowIn - carrierFlowOut
                - demand
                == 0)