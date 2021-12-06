# """===========================================================================================================================================================================
# Title:        ENERGY-CARBON OPTIMIZATION PLATFORM
# Created:      October-2021
# Authors:      Davide Tonelli (davidetonelli@outlook.com)
#               Alissa Ganter (aganter@ethz.ch)
# Organization: Laboratory of Risk and Reliability Engineering, ETH Zurich
#
# Description:  Compilation  of the optimization problem.
# ==========================================================================================================================================================================="""
import os
import logging
import numpy as np
import pandas as pd
from config import *
from model.preprocess.prepare import Prepare
from model.model_instance.model import Model
from model.postprocess.evaluation import Evaluation

# SETUP LOGGER
log_format = '%(asctime)s %(filename)s: %(message)s'
if not os.path.exists('outputs/logs'):
    os.mkdir('outputs/logs')
logging.basicConfig(filename='outputs/logs/valueChain.log', level=logging.CRITICAL, format=log_format, datefmt='%Y-%m-%d %H:%M:%S')
# prevent double printing
logging.propagate = False


# CREATE INPUT FILE
prepare = Prepare(analysis, system)

# FORMULATE AND SOLVE THE OPTIMIZATION PROBLEM
model = Model(analysis, system)
model.solve(solver, prepare.pyoDict)

# EVALUATE RESULTS
evaluation = Evaluation(system, model)