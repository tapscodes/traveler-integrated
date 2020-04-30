# Imports
import copy

import numpy as np
import json
from .loggers import logToConsole
from profiling_tools._cCalcBin import ffi, lib

class SparseUtilizationList():
    def __init__(self):
        self.locationDict = dict()
        self.cLocationDict = dict()

    # def __getitem__(self, loc):
    #     return self.locationDict[loc]
    #
    # def __setitem__(self, loc, val):
    #     self.locationDict[loc] = val

    def getCLocation(self, loc):
        return self.cLocationDict[loc]

    def setCLocation(self, loc, val):
        self.cLocationDict[loc] = copy.deepcopy(val)

    def sortAtLoc(self, loc):
        # self.locationDict[loc].sort(key=lambda x: x['index'])
        sorted(self.locationDict[loc], key=lambda x: x['index'])
        return

    def calcCurrentUtil(self, index, prior):
        if prior is None:
            last = {'index': 0, 'counter': 0, 'util': 0}
        else:
            last = prior

        return (((index - last['index']) * last['counter'])+last['util'])

    def setIntervalAtLocation(self, edgeUtilObj, location):
        # check if array exists
        if location not in self.locationDict:
            self.locationDict[location] = []

        self.locationDict[location].append(edgeUtilObj)
        return

    # Calculates utilization histogram for all intervals regardless of location
    def calcGanttHistogram(self, bins=100, begin=None, end=None):
        listOfLocations = []

        for location in self.locationDict:
            temp = self.calcUtilizationForLocation(bins, begin, end, location)
            listOfLocations.append({"location":location, "histogram":temp})

        return listOfLocations

    # Calculates utilization histogram for all intervals regardless of location
    def calcUtilizationHistogram(self, bins=100, begin=None, end=None, isInterval=True):

        array = []
        isFirst = True
        for location in self.locationDict:
            temp = self.calcUtilizationForLocation(bins, begin, end, location, isInterval)
            if isFirst is True:
                isFirst = False
                array = temp
            for i in range(bins):
                array[i] = array[i] + temp[i]

        return array

    # Calculates utilization histogram for all intervals regardless of location
    def calcMetricUtilization(self, bins=100, begin=None, end=None):
        array = []
        isFirst = True
        for location in self.locationDict:
            temp = self.calcUtilizationForLocation(bins, begin, end, location, False)
            if isFirst is True:
                isFirst = False
                array = temp
            else:
                array = array + temp

        return array

    # Calulates utilization for one location in a Gantt chart
    # Location designates a particular CPU or Thread and denotes the y-axis on the Gantt Chart
    def calcUtilizationForLocation(self, bins=100, begin=None, end=None, Location=None, isInterval=True):
        rangePerBin = (end-begin)/bins

        # caclulates the beginning of each each bin evenly divided over the range of
        # time indicies and stores them as critical points
        criticalPts = np.empty(bins + 1, dtype=np.int64)
        critical_length = len(criticalPts)
        critical_points = ffi.new("long long[]", critical_length)
        for i in range(0, bins):
            criticalPts[i] = (i * rangePerBin) + begin
            critical_points[i] = int((i * rangePerBin) + begin)
        criticalPts[len(criticalPts)-1] = end
        critical_points[len(criticalPts)-1] = end

        # searches
        histogram = np.empty_like(criticalPts, dtype=object)
        location = self.locationDict[Location]
        length = len(location)
        histogram_length = len(histogram)

        histogram_index = ffi.new("long long[]", histogram_length)
        histogram_counter = ffi.new("long long[]", histogram_length)
        histogram_util = ffi.new("double[]", histogram_length)

        # critical_points = ffi.new("int[]", critical_length)
        # for i in range(critical_length):
        #     critical_points[i] = criticalPts[i]

        cLocationStruct = self.getCLocation(Location)
        location_index = ffi.cast("long long*", cLocationStruct['index'].ctypes.data)
        location_counter = ffi.cast("long long*", cLocationStruct['counter'].ctypes.data)
        location_util = ffi.cast("double*", cLocationStruct['util'].ctypes.data)

        lib.calcHistogram(histogram_counter, histogram_length, histogram_index, histogram_util, critical_points, critical_length, location_index, length-1, location_counter, location_util)
        histogram[0] = {'integral': 0, 'index': histogram_index[0], 'util': histogram_util[0], 'counter': histogram_counter[0]}
        prev = histogram[0]
        prettyHistogram = []
        for i in range(1, len(histogram)):
            histogram[i] = {'index': histogram_index[i], 'util': histogram_util[i], 'counter': histogram_counter[i]}
            current = histogram[i]
            val = current['util']
            if isInterval:
                val = (current['util'] - prev['util']) / (current['index'] - prev['index'])
            current['integral'] = val
            prev = current
            prettyHistogram.append(histogram[i]['integral'])
        return prettyHistogram
# In charge of loading interval data into our integral list
# I have no idea how we want to load interval data :/
async def loadSUL(label, db, log=logToConsole):
    await log('Loading sparse utilization list.')

    # create sul obj
    sul = {'intervals': SparseUtilizationList(), 'metrics': dict()}
    begin = db[label]['meta']['intervalDomain'][0]
    end = db[label]['meta']['intervalDomain'][1]
    preMetricValue = dict()

    def updateSULForInterval(event):
        if 'metrics' in event:
            for k, value in event['metrics'].items():
                if k not in sul['metrics']:
                    sul['metrics'][k] = SparseUtilizationList()
                    preMetricValue[k] = {'Timestamp': 0, 'Value': 0}
                current_rate = (value - preMetricValue[k]['Value']) / (event['enter']['Timestamp'] - preMetricValue[k]['Timestamp'])
                sul['metrics'][k].setIntervalAtLocation({'index': int(event['enter']['Timestamp']), 'counter': 0, 'util': current_rate}, loc)
                preMetricValue[k]['Timestamp'] = event['enter']['Timestamp']
                preMetricValue[k]['Value'] = value

                current_rate = (value - preMetricValue[k]['Value']) / (event['leave']['Timestamp'] - preMetricValue[k]['Timestamp'])
                sul['metrics'][k].setIntervalAtLocation({'index': int(event['leave']['Timestamp']), 'counter': 0, 'util': current_rate}, loc)
                preMetricValue[k]['Timestamp'] = event['leave']['Timestamp']
                preMetricValue[k]['Value'] = value

    # we extract relevant data from database
    for loc in db[label]['intervalIndexes']['locations']:
        counter = 0
        for i in db[label]['intervalIndexes']['locations'][loc].iterOverlap(begin, end):
            sul['intervals'].setIntervalAtLocation({'index': int(i.begin), 'counter': 1, 'util': 0}, loc)
            sul['intervals'].setIntervalAtLocation({'index': int(i.end), 'counter': -1, 'util': 0}, loc)
            updateSULForInterval(db[label]['intervals'][i.data])
            # updateSULForInterval(db[label]['intervals'][i.data])

        # print('sul metric size: ' + str(len(sul['metrics'].items())))
        sul['intervals'].sortAtLoc(loc)
        sul['intervals'].locationDict[loc] = np.array(sul['intervals'].locationDict[loc])
        for key in sul['metrics']:
            sul['metrics'][key].sortAtLoc(loc)
            sul['metrics'][key].locationDict[loc] = np.array(sul['metrics'][key].locationDict[loc])

        length = len(sul['intervals'].locationDict[loc])
        for i, criticalPt in enumerate(sul['intervals'].locationDict[loc]):
            counter += criticalPt['counter']
            criticalPt['counter'] = counter
            if i is 0:
                criticalPt['util'] = sul['intervals'].calcCurrentUtil(criticalPt['index'], None)
            else:
                criticalPt['util'] = sul['intervals'].calcCurrentUtil(criticalPt['index'], sul['intervals'].locationDict[loc][i-1])

        locStruct = {'index': np.empty(length, dtype=np.int64), 'counter': np.empty(length, dtype=np.int64), 'util': np.zeros(length, dtype=np.double)}
        for i in range(length):
            locStruct['index'][i] = sul['intervals'].locationDict[loc][i]['index']
            locStruct['counter'][i] = sul['intervals'].locationDict[loc][i]['counter']
            locStruct['util'][i] = sul['intervals'].locationDict[loc][i]['util']

        sul['intervals'].setCLocation(loc, locStruct)
        # print("interval loc struct initiated")

        for key in sul['metrics']:
            length = len(sul['metrics'][key].locationDict[loc])
            mlocStruct = {'index': np.empty(length, dtype=np.int64), 'counter': np.empty(length, dtype=np.int64), 'util': np.zeros(length, dtype=np.double)}
            for i in range(length):
                mlocStruct['index'][i] = sul['metrics'][key].locationDict[loc][i]['index']
                mlocStruct['counter'][i] = sul['metrics'][key].locationDict[loc][i]['counter']
                mlocStruct['util'][i] = sul['metrics'][key].locationDict[loc][i]['util']

            sul['metrics'][key].setCLocation(loc, mlocStruct)
        # print("metric loc struct initiated")
    db[label]['sparseUtilizationList'] = sul

    return
