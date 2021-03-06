#
#   vanilla_stats_parser.py
#
#   vanilla core stats extractor
# 
#   input: vanilla_stats.log
#   output: stats/manycore_stats.log
#   output: stats/tile/tile_<x>_<y>_stats.log for all tiles 
#   output: stats/tile_group/tile_group_<tg_id>_stats.log for all tile groups
#
#   @author Borna
#
#   How to use:
#   python3 vanilla_stats_parser.py --dim-y {manycore_dim_y}  --dim-x {manycore_dim_x} 
#                             --tile (optional) --tile_group (optional)
#                             --input {vanilla_stats.csv}
#
#   ex) python3 --input vanilla_stats_parser.py --dim-y 4 --dim-x 4 --tile --tile_group --input vanilla_stats.csv
#
#   {manycore_dim_y}  Mesh Y dimension of manycore 
#   {manycore_dim_x}  Mesh X dimension of manycore 
#   {per_tile}        Generate separate stats file for each tile default = False
#   {input}           Vanilla stats input file     default = vanilla_stats.log



import sys
import argparse
import os
import re
import csv
import numpy as np
from enum import Enum
from collections import Counter



BSG_PRINT_STAT_KERNEL_TAG = 0x0



# CudaStatTag class
# Is instantiated by a packet tag value that is recieved from a 
# bsg_cuda_print_stat(tag) insruction
# Breaks down the tag into (type, y, x, tg_id, tag>
# type of tag could be start, end, stat
# x,y are coordinates of the tile that triggered the print_stat instruciton
# tg_id is the tile group id of the tile that triggered the print_stat instruction
# Formatting for bsg_cuda_print_stat instructions
# Section                 stat type  -   y cord   -   x cord   -    tile group id   -        tag
# of bits                <----2----> -   <--6-->  -   <--6-->  -   <------14----->  -   <-----4----->
# Stat type value: {"stat":0, "start":1, "end":2}
class CudaStatTag:
    # These values are used by the manycore library in bsg_print_stat instructions
    # they are added to the tag value to determine the tile group that triggered the stat
    # and also the type of stat (stand-alone stat, start, or end)
    # the value of these paramters should match their counterpart inside 
    # bsg_manycore/software/bsg_manycore_lib/bsg_manycore.h
    # For formatting, see the CudaStatTag class
    _TAG_WIDTH   = 4
    _TAG_INDEX   = 0
    _TAG_MASK   = ((1 << _TAG_WIDTH) - 1)
    _TG_ID_WIDTH = 14
    _TG_ID_INDEX = _TAG_WIDTH + _TAG_INDEX
    _TG_ID_MASK = ((1 << _TG_ID_WIDTH) - 1)
    _X_WIDTH     = 6
    _X_MASK     = ((1 << _X_WIDTH) - 1)
    _X_INDEX     = _TG_ID_WIDTH + _TG_ID_INDEX
    _Y_WIDTH     = 6
    _Y_INDEX     = _X_WIDTH + _X_INDEX
    _Y_MASK     = ((1 << _Y_WIDTH) - 1)
    _TYPE_WIDTH  = 2
    _TYPE_INDEX  = _Y_WIDTH + _Y_INDEX
    _TYPE_MASK   = ((1 << _TYPE_WIDTH) - 1)

    class StatType(Enum):
        STAT  = 0
        START = 1
        END   = 2

    def __init__(self, tag):
        self.__s = tag;
        self.__type = self.StatType((self.__s >> self._TYPE_INDEX) & self._TYPE_MASK)

    @property 
    def tag(self):
        return ((self.__s >> self._TAG_INDEX) & self._TAG_MASK)

    @property 
    def tg_id(self):
        return ((self.__s >> self._TG_ID_INDEX) & self._TG_ID_MASK)

    @property 
    def x(self):
        return ((self.__s >> self._X_INDEX) & self._X_MASK)

    @property 
    def y(self):
        return ((self.__s >> self._Y_INDEX) & self._Y_MASK)

    @property 
    def statType(self):
        return self.__type

    @property 
    def isStart(self):
        return (self.__type == self.StatType.START)

    @property 
    def isEnd(self):
        return (self.__type == self.StatType.END)

    @property 
    def isStat(self):
        return (self.__type == self.StatType.STAT)



 
class VanillaStatsParser:
    # Default coordinates of origin tile
    _BSG_ORIGIN_X = 0
    _BSG_ORIGIN_Y = 1

    # formatting parameters for aligned printing
    type_fmt = {"name"      : "{:<35}",
                "type"      : "{:>20}",
                "int"       : "{:>20}",
                "float"     : "{:>20.4f}",
                "percent"   : "{:>20.2f}",
                "cord"      : "{:<2}, {:<31}",
                "tag"       : "Tag {:<2}",
               }


    print_format = {"tg_timing_header": type_fmt["name"] + type_fmt["type"] + type_fmt["type"]    + type_fmt["type"]    + type_fmt["type"]    + type_fmt["type"]    + "\n",
                    "tg_timing_data"  : type_fmt["name"] + type_fmt["int"]  + type_fmt["int"]     + type_fmt["float"]   + type_fmt["percent"] + type_fmt["percent"] + "\n",
                    "timing_header"   : type_fmt["name"] + type_fmt["type"] + type_fmt["type"]    + type_fmt["type"]    + type_fmt["type"]    + type_fmt["type"]    + "\n",
                    "timing_data"     : type_fmt["cord"] + type_fmt["int"]  + type_fmt["int"]     + type_fmt["float"]   + type_fmt["percent"] + type_fmt["percent"] + "\n",
                    "instr_header"    : type_fmt["name"] + type_fmt["int"]  + type_fmt["type"]    + "\n",
                    "instr_data"      : type_fmt["name"] + type_fmt["int"]  + type_fmt["percent"] + "\n",
                    "stall_header"    : type_fmt["name"] + type_fmt["type"] + type_fmt["type"]    + type_fmt["type"]    + "\n",
                    "stall_data"      : type_fmt["name"] + type_fmt["int"]  + type_fmt["percent"] + type_fmt["percent"] + "\n",
                    "miss_header"     : type_fmt["name"] + type_fmt["type"] + type_fmt["type"]    + type_fmt["type"]    + "\n",
                    "miss_data"       : type_fmt["name"] + type_fmt["int"]  + type_fmt["int"]     + type_fmt["float"]   + "\n",
                    "tag_header"      : type_fmt["name"] + type_fmt["type"] + type_fmt["type"]    + type_fmt["type"]    + type_fmt["type"]    + type_fmt["type"] + "\n",
                    "tag_data"        : type_fmt["name"] + type_fmt["int"]  + type_fmt["int"]     + type_fmt["int"]     + type_fmt["float"]   + type_fmt["percent"] + "\n",
                    "tag_separator"   : '-' * 75 + ' ' * 2 + type_fmt["tag"]  + ' ' * 2 + '-' * 75 + "\n",
                    "start_lbreak"    : '=' *160 + "\n",
                    "end_lbreak"      : '=' *160 + "\n\n",
                   }



    # default constructor
    def __init__(self, manycore_dim_y, manycore_dim_x, per_tile_stat, per_tile_group_stat, input_file):

        self.manycore_dim_y = manycore_dim_y
        self.manycore_dim_x = manycore_dim_x
        self.manycore_dim = manycore_dim_y * manycore_dim_x
        self.per_tile_stat = per_tile_stat
        self.per_tile_group_stat = per_tile_group_stat

        self.traces = []

        self.max_tile_groups = 1 << CudaStatTag._TG_ID_WIDTH
        self.num_tile_groups = []

        self.max_tags = 1 << CudaStatTag._TAG_WIDTH
        self.num_tags = 0

        self.tile_stat = [Counter() for tag in range(self.max_tags)]
        self.tile_group_stat = [Counter() for tag in range(self.max_tags)]
        self.manycore_stat = [Counter() for tag in range(self.max_tags)]


        # list of instructions, operations and events parsed from vanilla_stats.log
        # populated by reading the header of input file 
        self.stats_list   = []
        self.instrs = []
        self.misses = []
        self.stalls = []
        self.all_ops = []

        # Parse input file's header to generate a list of all types of operations
        self.stats, self.instrs, self.misses, self.stalls = self.parse_header(input_file)
        self.all_ops = self.stats + self.instrs + self.misses + self.stalls

        # Parse stats file line by line, and append the trace line to traces list. 
        with open(input_file) as f:
            csv_reader = csv.DictReader (f, delimiter=",")
            for row in csv_reader:
                trace = {}
                for op in self.all_ops:
                    trace[op] = int(row[op])
                self.traces.append(trace)

        # generate timing stats for each tile and tile group 
        self.num_tags, self.num_tile_groups, self.tile_group_stat, self.tile_stat = self.__generate_tile_stats(self.traces)

        # Calculate total aggregate stats for manycore
        # By summing up per_tile stat counts
        self.manycore_stat = self.__generate_manycore_stats_all(self.tile_stat)

        return


    # print a line of stat into stats file based on stat type
    def __print_stat(self, stat_file, stat_type, *argv):
        if (stat_type == "tag_separator" and argv[0] == 0):
            stat_file.write(self.print_format[stat_type].format("kernel"));
            return
        stat_file.write(self.print_format[stat_type].format(*argv));
        return



    # print instruction count, stall count, execution cycles for the entire manycore for each tag
    def __print_manycore_stats_tag(self, stat_file):
        stat_file.write("Tag Stats\n")
        self.__print_stat(stat_file, "tag_header", "tag", "instr", "stall", "cycle sum", "IPC", "cycle share(%)")
        self.__print_stat(stat_file, "start_lbreak")

        for tag in range (self.max_tags):
            if(self.manycore_stat[tag]["global_ctr"]):
                self.__print_stat(stat_file, "tag_data"
                                             ,tag
                                             ,self.manycore_stat[tag]["instr_total"]
                                             ,self.manycore_stat[tag]["stall_total"]
                                             ,self.manycore_stat[tag]["global_ctr"]
                                             ,(np.float64(self.manycore_stat[tag]["instr_total"]) / self.manycore_stat[tag]["global_ctr"])
                                             ,(100 * self.manycore_stat[tag]["global_ctr"] / self.manycore_stat[BSG_PRINT_STAT_KERNEL_TAG]["global_ctr"]))
        self.__print_stat(stat_file, "end_lbreak")
        return




    # print instruction count, stall count, execution cycles 
    # for each tile group in a separate file for each tag
    def __print_per_tile_group_stats_tag(self, tg_id, stat_file):
        stat_file.write("Tag Stats\n")
        self.__print_stat(stat_file, "tag_header", "tag", "instr", "stall", "cycle sum", "IPC", "cycle share(%)")
        self.__print_stat(stat_file, "start_lbreak")

        for tag in range (self.max_tags):
            if(self.tile_group_stat[tag][tg_id]["global_ctr"]):
                self.__print_stat(stat_file, "tag_data"
                                             ,tag
                                             ,self.tile_group_stat[tag][tg_id]["instr_total"]
                                             ,self.tile_group_stat[tag][tg_id]["stall_total"]
                                             ,self.tile_group_stat[tag][tg_id]["global_ctr"]
                                             ,(np.float64(self.tile_group_stat[tag][tg_id]["instr_total"]) / self.tile_group_stat[tag][tg_id]["global_ctr"])
                                             ,(100 * self.tile_group_stat[tag][tg_id]["global_ctr"] / self.tile_group_stat[BSG_PRINT_STAT_KERNEL_TAG][tg_id]["global_ctr"]))
        self.__print_stat(stat_file, "end_lbreak")
        return




    # print instruction count, stall count, execution cycles 
    # for each tile in a separate file for each tag
    def __print_per_tile_stats_tag(self, y, x, stat_file):
        stat_file.write("Tag Stats\n")
        self.__print_stat(stat_file, "tag_header", "tag", "instr", "stall", "cycle sum", "IPC", "cycle share(%)")
        self.__print_stat(stat_file, "start_lbreak")

        for tag in range (self.max_tags):
            if(self.tile_stat[tag][y][x]["global_ctr"]):
                self.__print_stat(stat_file, "tag_data"
                                             ,tag
                                             ,self.tile_stat[tag][y][x]["instr_total"]
                                             ,self.tile_stat[tag][y][x]["stall_total"]
                                             ,self.tile_stat[tag][y][x]["global_ctr"]
                                             ,(np.float64(self.tile_stat[tag][y][x]["instr_total"]) / self.tile_stat[tag][y][x]["global_ctr"])
                                             ,(100 * self.tile_stat[tag][y][x]["global_ctr"] / self.tile_stat[BSG_PRINT_STAT_KERNEL_TAG][y][x]["global_ctr"]))
        self.__print_stat(stat_file, "end_lbreak")
        return




    # print execution timing for the entire manycore per tile group for a certain tag
    def __print_manycore_tag_stats_tile_group_timing(self, stat_file, tag):
        self.__print_stat(stat_file, "tag_separator", tag)

        for tg_id in range (0, self.num_tile_groups[tag]):
            self.__print_stat(stat_file, "tg_timing_data"
                                         ,tg_id
                                         ,(self.tile_group_stat[tag][tg_id]["instr_total"])
                                         ,(self.tile_group_stat[tag][tg_id]["global_ctr"])
                                         ,(np.float64(self.tile_group_stat[tag][tg_id]["instr_total"]) / self.tile_group_stat[tag][tg_id]["global_ctr"])
                                         ,(100 * self.tile_group_stat[tag][tg_id]["global_ctr"] / self.manycore_stat[tag]["global_ctr"])
                                         ,(100 * np.float64(self.tile_group_stat[tag][tg_id]["global_ctr"]) / self.tile_group_stat[BSG_PRINT_STAT_KERNEL_TAG][tg_id]["global_ctr"]))

        self.__print_stat(stat_file, "tg_timing_data"
                                     ,"total"
                                     ,(self.manycore_stat[tag]["instr_total"])
                                     ,(self.manycore_stat[tag]["global_ctr"])
                                     ,(self.manycore_stat[tag]["instr_total"] / self.manycore_stat[tag]["global_ctr"])
                                     ,(100 * self.manycore_stat[tag]["instr_total"] / self.manycore_stat[tag]["instr_total"])
                                     ,(100 * self.manycore_stat[tag]["global_ctr"] / self.manycore_stat[BSG_PRINT_STAT_KERNEL_TAG]["global_ctr"]))
        return


    # Prints manycore timing stats per tile group for all tags 
    def __print_manycore_stats_tile_group_timing(self, stat_file):
        stat_file.write("Tile Group Timing Stats\n")
        self.__print_stat(stat_file, "tg_timing_header", "tile group", "instr sum", "cycle sum", "IPC", "TG&tag / tag(%)", "TG&tag / TG&kernel(%)")
        self.__print_stat(stat_file, "start_lbreak")
        for tag in range(self.max_tags):
            if(self.manycore_stat[tag]["global_ctr"]):
                self.__print_manycore_tag_stats_tile_group_timing(stat_file, tag)
        self.__print_stat(stat_file, "end_lbreak")
        return   




    # print execution timing for the entire manycore per tile
    def __print_manycore_tag_stats_tile_timing(self, stat_file, tag):
        self.__print_stat(stat_file, "tag_separator", tag)

        for y in range(self.manycore_dim_y):
            for x in range(self.manycore_dim_x):
                self.__print_stat(stat_file, "timing_data"
                                             ,y
                                             ,x
                                             ,(self.tile_stat[tag][y][x]["instr_total"])
                                             ,(self.tile_stat[tag][y][x]["global_ctr"])
                                             ,(np.float64(self.tile_stat[tag][y][x]["instr_total"]) / self.tile_stat[tag][y][x]["global_ctr"])
                                             ,(100 * self.tile_stat[tag][y][x]["global_ctr"] / self.manycore_stat[tag]["global_ctr"])
                                             ,(100 * np.float64(self.tile_stat[tag][y][x]["global_ctr"]) / self.tile_stat[BSG_PRINT_STAT_KERNEL_TAG][y][x]["global_ctr"]))

        self.__print_stat(stat_file, "tg_timing_data"
                                     ,"total"
                                     ,(self.manycore_stat[tag]["instr_total"])
                                     ,(self.manycore_stat[tag]["global_ctr"])
                                     ,(self.manycore_stat[tag]["instr_total"] / self.manycore_stat[tag]["global_ctr"])
                                     ,(100 * self.manycore_stat[tag]["global_ctr"] / self.manycore_stat[tag]["global_ctr"])
                                     ,(100 * self.manycore_stat[tag]["global_ctr"] / self.manycore_stat[BSG_PRINT_STAT_KERNEL_TAG]["global_ctr"]))
        return


    # Prints manycore timing stats per tile group for all tags 
    def __print_manycore_stats_tile_timing(self, stat_file):
        stat_file.write("Tile Timing Stats\n")
        self.__print_stat(stat_file, "timing_header", "tile", "instr", "cycle", "IPC", "tile&tag / tag(%)", "tile&tag / tile&kernel(%)")
        self.__print_stat(stat_file, "start_lbreak")
        for tag in range(self.max_tags):
            if(self.manycore_stat[tag]["global_ctr"]):
                self.__print_manycore_tag_stats_tile_timing(stat_file, tag)
        self.__print_stat(stat_file, "end_lbreak")
        return   




    # print timing stats for each tile group in a separate file 
    # tg_id is tile group id 
    def __print_per_tile_group_tag_stats_timing(self, tg_id, stat_file, tag):
        self.__print_stat(stat_file, "tag_separator", tag)

        self.__print_stat(stat_file, "tg_timing_data"
                                     ,tg_id
                                     ,(self.tile_group_stat[tag][tg_id]["instr_total"])
                                     ,(self.tile_group_stat[tag][tg_id]["global_ctr"])
                                     ,(np.float64(self.tile_group_stat[tag][tg_id]["instr_total"]) / self.tile_group_stat[tag][tg_id]["global_ctr"])
                                     ,(100 * self.tile_group_stat[tag][tg_id]["global_ctr"] / self.manycore_stat[tag]["global_ctr"])
                                     ,(100 * np.float64(self.tile_group_stat[tag][tg_id]["instr_total"]) / self.tile_group_stat[BSG_PRINT_STAT_KERNEL_TAG][tg_id]["instr_total"]))
        return


    # Print timing stat for each tile group in separate file for all tags 
    def __print_per_tile_group_stats_timing(self, tg_id, stat_file):
        stat_file.write("Timing Stats\n")
        self.__print_stat(stat_file, "tg_timing_header", "tile group", "instr sum", "cycle sum", "IPC", "TG&tag / tag(%)", "TG&tag / TG&kernel(%)")
        self.__print_stat(stat_file, "start_lbreak")
        for tag in range(self.max_tags):
            if(self.tile_group_stat[tag][tg_id]["global_ctr"]):
                self.__print_per_tile_group_tag_stats_timing(tg_id, stat_file, tag)
        self.__print_stat(stat_file, "end_lbreak")
        return   




    # print timing stats for each tile in a separate file 
    # y,x are tile coordinates 
    def __print_per_tile_tag_stats_timing(self, y, x, stat_file, tag):
        self.__print_stat(stat_file, "tag_separator", tag)

        self.__print_stat(stat_file, "timing_data"
                                     ,y
                                     ,x
                                     ,(self.tile_stat[tag][y][x]["instr_total"])
                                     ,(self.tile_stat[tag][y][x]["global_ctr"])
                                     ,(np.float64(self.tile_stat[tag][y][x]["instr_total"]) / self.tile_stat[tag][y][x]["global_ctr"])
                                     ,(100 * self.tile_stat[tag][y][x]["global_ctr"] / self.manycore_stat[tag]["global_ctr"])
                                     ,(100 * self.tile_stat[tag][y][x]["global_ctr"] / self.tile_stat[BSG_PRINT_STAT_KERNEL_TAG][y][x]["global_ctr"]))

        return


    # print timing stats for each tile in a separate file for all tags 
    def __print_per_tile_stats_timing(self, y, x, stat_file):
        stat_file.write("Timing Stats\n")
        self.__print_stat(stat_file, "timing_header", "tile", "instr", "cycle", "IPC", "tile&tag / tag(%)", "tile&tag / tile&kernel(%)")
        self.__print_stat(stat_file, "start_lbreak")
        for tag in range(self.max_tags):
            if(self.tile_stat[tag][y][x]["global_ctr"]):
                self.__print_per_tile_tag_stats_timing(y, x, stat_file, tag)
        self.__print_stat(stat_file, "end_lbreak")
        return   




    # print instruction stats for the entire manycore
    def __print_manycore_tag_stats_instr(self, stat_file, tag):
        self.__print_stat(stat_file, "tag_separator", tag)
   
        # Print instruction stats for manycore
        for instr in self.instrs:
            self.__print_stat(stat_file, "instr_data", instr,
                                         self.manycore_stat[tag][instr]
                                         ,(100 * self.manycore_stat[tag][instr] / self.manycore_stat[tag]["instr_total"]))
#                                         ,(100 * np.float64(self.manycore_stat[tag][instr]) / self.manycore_stat[BSG_PRINT_STAT_KERNEL_TAG][instr]))
        return


    # Prints manycore instruction stats per tile group for all tags 
    def __print_manycore_stats_instr(self, stat_file):
        stat_file.write("Instruction Stats\n")
        self.__print_stat(stat_file, "instr_header", "instruction", "count", "tag instr mix(%)")
        self.__print_stat(stat_file, "start_lbreak")
        for tag in range(self.max_tags):
            if(self.manycore_stat[tag]["global_ctr"]):
                self.__print_manycore_tag_stats_instr(stat_file, tag)
        self.__print_stat(stat_file, "end_lbreak")
        return   




    # print instruction stats for each tile group in a separate file 
    # tg_id is tile group id 
    def __print_per_tile_group_tag_stats_instr(self, tg_id, stat_file, tag):
        self.__print_stat(stat_file, "tag_separator", tag)

        # Print instruction stats for manycore
        for instr in self.instrs:
            self.__print_stat(stat_file, "instr_data", instr,
                                         self.tile_group_stat[tag][tg_id][instr]
                                         ,(100 * self.tile_group_stat[tag][tg_id][instr] / self.tile_group_stat[tag][tg_id]["instr_total"]))
#                                         ,(100 * np.float64(self.tile_group_stat[tag][tg_id][instr]) / self.tile_group_stat[BSG_PRINT_STAT_KERNEL_TAG][tg_id][instr]))
        return


    # Print instruction stat for each tile group in separate file for all tags 
    def __print_per_tile_group_stats_instr(self, tg_id, stat_file):
        stat_file.write("Instruction Stats\n")
        self.__print_stat(stat_file, "instr_header", "instruction", "count", "tag instr mix(%)")
        self.__print_stat(stat_file, "start_lbreak")
        for tag in range(self.max_tags):
            if(self.tile_group_stat[tag][tg_id]["global_ctr"]):
                self.__print_per_tile_group_tag_stats_instr(tg_id, stat_file, tag)
        self.__print_stat(stat_file, "end_lbreak")
        return   




    # print instruction stats for each tile in a separate file 
    # y,x are tile coordinates 
    def __print_per_tile_tag_stats_instr(self, y, x, stat_file, tag):
        self.__print_stat(stat_file, "tag_separator", tag)

        # Print instruction stats for manycore
        for instr in self.instrs:
            self.__print_stat(stat_file, "instr_data", instr,
                                         self.tile_stat[tag][y][x][instr]
                                         ,(100 * np.float64(self.tile_stat[tag][y][x][instr]) / self.tile_stat[tag][y][x]["instr_total"]))
#                                         ,(100 * np.float64(self.tile_stat[tag][y][x][instr]) / self.tile_stat[BSG_PRINT_STAT_KERNEL_TAG][y][x][instr]))
        return


    # print instr stats for each tile in a separate file for all tags 
    def __print_per_tile_stats_instr(self, y, x, stat_file):
        stat_file.write("Instruction Stats\n")
        self.__print_stat(stat_file, "instr_header", "instruction", "count", "tag instr mix(%)")
        self.__print_stat(stat_file, "start_lbreak")
        for tag in range(self.max_tags):
            if(self.tile_stat[tag][y][x]["global_ctr"]):
                self.__print_per_tile_tag_stats_instr(y, x, stat_file, tag)
        self.__print_stat(stat_file, "end_lbreak")
        return   




    # print stall stats for the entire manycore
    def __print_manycore_tag_stats_stall(self, stat_file, tag):
        self.__print_stat(stat_file, "tag_separator", tag)

        # Print stall stats for manycore
        for stall in self.stalls:
            self.__print_stat(stat_file, "stall_data", stall,
                                         self.manycore_stat[tag][stall],
                                         (100 * self.manycore_stat[tag][stall] / self.manycore_stat[tag]["stall_total"])
                                         ,(100 * self.manycore_stat[tag][stall] / self.manycore_stat[tag]["global_ctr"]))
#                                         ,(100 * np.float64(self.manycore_stat[tag][stall]) / self.manycore_stat[BSG_PRINT_STAT_KERNEL_TAG][stall]))
        return


    # Prints manycore stall stats per tile group for all tags 
    def __print_manycore_stats_stall(self, stat_file):
        stat_file.write("Stall Stats\n")
        self.__print_stat(stat_file, "stall_header", "stall", "cycles", "tag stall mix(%)", "cycle share(%)")
        self.__print_stat(stat_file, "start_lbreak")
        for tag in range(self.max_tags):
            if(self.manycore_stat[tag]["global_ctr"]):
                self.__print_manycore_tag_stats_stall(stat_file, tag)
        self.__print_stat(stat_file, "end_lbreak")
        return   




    # print stall stats for each tile group in a separate file
    # tg_id is tile group id  
    def __print_per_tile_group_tag_stats_stall(self, tg_id, stat_file, tag):
        self.__print_stat(stat_file, "tag_separator", tag)

        # Print stall stats for manycore
        for stall in self.stalls:
            self.__print_stat(stat_file, "stall_data"
                                         ,stall
                                         ,self.tile_group_stat[tag][tg_id][stall]
                                         ,(100 * self.tile_group_stat[tag][tg_id][stall] / self.tile_group_stat[tag][tg_id]["stall_total"])
                                         ,(100 * self.tile_group_stat[tag][tg_id][stall] / self.tile_group_stat[tag][tg_id]["global_ctr"]))
#                                         ,(100 * np.float64(self.tile_group_stat[tag][tg_id][stall]) / self.tile_group_stat[BSG_PRINT_STAT_KERNEL_TAG][tg_id][stall]))
        return


    # Print stall stat for each tile group in separate file for all tags 
    def __print_per_tile_group_stats_stall(self, tg_id, stat_file):
        stat_file.write("Stall Stats\n")
        self.__print_stat(stat_file, "stall_header", "stall", "cycles", "tag stall mix(%)", "cycle share(%)")
        self.__print_stat(stat_file, "start_lbreak")
        for tag in range(self.max_tags):
            if(self.tile_group_stat[tag][tg_id]["global_ctr"]):
                self.__print_per_tile_group_tag_stats_stall(tg_id, stat_file, tag)
        self.__print_stat(stat_file, "end_lbreak")
        return   




    # print stall stats for each tile in a separate file
    # y,x are tile coordinates 
    def __print_per_tile_tag_stats_stall(self, y, x, stat_file, tag):
        self.__print_stat(stat_file, "tag_separator", tag)

        # Print stall stats for manycore
        for stall in self.stalls:
            self.__print_stat(stat_file, "stall_data", stall,
                                         self.tile_stat[tag][y][x][stall],
                                         (100 * np.float64(self.tile_stat[tag][y][x][stall]) / self.tile_stat[tag][y][x]["stall_total"])
                                         ,(100 * np.float64(self.tile_stat[tag][y][x][stall]) / self.tile_stat[tag][y][x]["global_ctr"]))
#                                         ,(100 * np.float64(self.tile_stat[tag][y][x][stall]) / self.tile_stat[BSG_PRINT_STAT_KERNEL_TAG][y][x][stall]))
        return


    # print stall stats for each tile in a separate file for all tags 
    def __print_per_tile_stats_stall(self, y, x, stat_file):
        stat_file.write("Stall Stats\n")
        self.__print_stat(stat_file, "stall_header", "stall", "cycles", "tag stall mix(%)", "cycle share(%)")
        self.__print_stat(stat_file, "start_lbreak")
        for tag in range(self.max_tags):
            if(self.tile_stat[tag][y][x]["global_ctr"]):
                self.__print_per_tile_tag_stats_stall(y, x, stat_file, tag)
        self.__print_stat(stat_file, "start_lbreak")
        return   




    # print miss stats for the entire manycore
    def __print_manycore_tag_stats_miss(self, stat_file, tag):
        self.__print_stat(stat_file, "tag_separator", tag)

        for miss in self.misses:
            # Find total number of operations for that miss
            # If operation is icache, the total is total # of instruction
            # otherwise, search for the specific instruction
            if (miss == "miss_icache"):
                operation = "icache"
                operation_cnt = self.manycore_stat[tag]["instr_total"]
            else:
                operation = miss.replace("miss_", "instr_")
                operation_cnt = self.manycore_stat[tag][operation]
            miss_cnt = self.manycore_stat[tag][miss]
            hit_rate = 1 if operation_cnt == 0 else (1 - miss_cnt/operation_cnt)
         
            self.__print_stat(stat_file, "miss_data", miss, miss_cnt, operation_cnt, hit_rate )
        return


    # Prints manycore miss stats per tile group for all tags 
    def __print_manycore_stats_miss(self, stat_file):
        stat_file.write("Miss Stats\n")
        self.__print_stat(stat_file, "miss_header", "unit", "miss", "total", "hit rate")
        self.__print_stat(stat_file, "start_lbreak")
        for tag in range(self.max_tags):
            if(self.manycore_stat[tag]["global_ctr"]):
                self.__print_manycore_tag_stats_miss(stat_file, tag)
        self.__print_stat(stat_file, "end_lbreak")
        return   




    # print miss stats for each tile group in a separate file
    # tg_id is tile group id  
    def __print_per_tile_group_tag_stats_miss(self, tg_id, stat_file, tag):
        self.__print_stat(stat_file, "tag_separator", tag)

        for miss in self.misses:
            # Find total number of operations for that miss
            # If operation is icache, the total is total # of instruction
            # otherwise, search for the specific instruction
            if (miss == "miss_icache"):
                operation = "icache"
                operation_cnt = self.tile_group_stat[tag][tg_id]["instr_total"]
            else:
                operation = miss.replace("miss_", "instr_")
                operation_cnt = self.tile_group_stat[tag][tg_id][operation]
            miss_cnt = self.tile_group_stat[tag][tg_id][miss]
            hit_rate = 1 if operation_cnt == 0 else (1 - miss_cnt/operation_cnt)

            self.__print_stat(stat_file, "miss_data", miss, miss_cnt, operation_cnt, hit_rate )

        return

    # Print miss stat for each tile group in separate file for all tags 
    def __print_per_tile_group_stats_miss(self, tg_id, stat_file):
        stat_file.write("Miss Stats\n")
        self.__print_stat(stat_file, "miss_header", "unit", "miss", "total", "hit rate")
        self.__print_stat(stat_file, "start_lbreak")
        for tag in range(self.max_tags):
            if(self.tile_group_stat[tag][tg_id]["global_ctr"]):
                self.__print_per_tile_group_tag_stats_miss(tg_id, stat_file, tag)
        self.__print_stat(stat_file, "end_lbreak")
        return   




    # print miss stats for each tile in a separate file
    # y,x are tile coordinates 
    def __print_per_tile_tag_stats_miss(self, y, x, stat_file, tag):
        self.__print_stat(stat_file, "tag_separator", tag)

        for miss in self.misses:
            # Find total number of operations for that miss
            # If operation is icache, the total is total # of instruction
            # otherwise, search for the specific instruction
            if (miss == "miss_icache"):
                operation = "icache"
                operation_cnt = self.tile_stat[tag][y][x]["instr_total"]
            else:
                operation = miss.replace("miss_", "instr_")
                operation_cnt = self.tile_stat[tag][y][x][operation]
            miss_cnt = self.tile_stat[tag][y][x][miss]
            hit_rate = 1 if operation_cnt == 0 else (1 - miss_cnt/operation_cnt)
         
            self.__print_stat(stat_file, "miss_data", miss, miss_cnt, operation_cnt, hit_rate )

        return


    # print stall miss for each tile in a separate file for all tags 
    def __print_per_tile_stats_miss(self, y, x, stat_file):
        stat_file.write("Miss Stats\n")
        self.__print_stat(stat_file, "miss_header", "unit", "miss", "total", "hit rate")
        self.__print_stat(stat_file, "start_lbreak")
        for tag in range(self.max_tags):
            if(self.tile_stat[tag][y][x]["global_ctr"]):
                self.__print_per_tile_tag_stats_miss(y, x, stat_file, tag)
        self.__print_stat(stat_file, "end_lbreak")
        return   




    # prints all four types of stats, timing, instruction,
    # miss and stall for the entire manycore 
    def print_manycore_stats_all(self):
        stats_path = os.getcwd() + "/stats/"
        if not os.path.exists(stats_path):
            os.mkdir(stats_path)
        manycore_stats_file = open( (stats_path + "manycore_stats.log"), "w")
        self.__print_manycore_stats_tag(manycore_stats_file)
        self.__print_manycore_stats_tile_group_timing(manycore_stats_file)
        self.__print_manycore_stats_miss(manycore_stats_file)
        self.__print_manycore_stats_stall(manycore_stats_file)
        self.__print_manycore_stats_instr(manycore_stats_file)
        self.__print_manycore_stats_tile_timing(manycore_stats_file)
        manycore_stats_file.close()
        return

    # prints all four types of stats, timing, instruction,
    # miss and stall for each tile group in a separate file  
    def print_per_tile_group_stats_all(self):
        stats_path = os.getcwd() + "/stats/tile_group/"
        if not os.path.exists(stats_path):
            os.mkdir(stats_path)
        for tg_id in range(max(self.num_tile_groups)):
            stat_file = open( (stats_path + "tile_group_" + str(tg_id) + "_stats.log"), "w")
            self.__print_per_tile_group_stats_tag(tg_id, stat_file)
            self.__print_per_tile_group_stats_timing(tg_id, stat_file)
            self.__print_per_tile_group_stats_miss(tg_id, stat_file)
            self.__print_per_tile_group_stats_stall(tg_id, stat_file)
            self.__print_per_tile_group_stats_instr(tg_id, stat_file)
            stat_file.close()
        return



    # prints all four types of stats, timing, instruction,
    # miss and stall for each tile in a separate file  
    def print_per_tile_stats_all(self):
        stats_path = os.getcwd() + "/stats/tile/"
        if not os.path.exists(stats_path):
            os.mkdir(stats_path)
        for y in range(self.manycore_dim_y):
            for x in range(self.manycore_dim_x):
                stat_file = open( (stats_path + "tile_" + str(y) + "_" + str(x) + "_stats.log"), "w")
                self.__print_per_tile_stats_tag(y, x, stat_file)
                self.__print_per_tile_stats_timing(y, x, stat_file)
                self.__print_per_tile_stats_miss(y, x, stat_file)
                self.__print_per_tile_stats_stall(y, x, stat_file)
                self.__print_per_tile_stats_instr(y, x, stat_file)
                stat_file.close()







    # go though the input traces and extract start and end stats  
    # for each tile, and each tile group 
    # return number of tile groups, tile group timing stats, and the tile stats
    # this function only counts the portion between two print_stat_start and end messages
    # in practice, this excludes the time in between executions,
    # i.e. when tiles are waiting to be loaded by the host.
    def __generate_tile_stats(self, traces):
        num_tags = 0
        num_tile_groups = [0 for tag in range(self.max_tags)]

        tile_stat_start = [[[Counter() for x in range(self.manycore_dim_x)] for y in range(self.manycore_dim_y)] for tag in range(self.max_tags)]
        tile_stat_end   = [[[Counter() for x in range(self.manycore_dim_x)] for y in range(self.manycore_dim_y)] for tag in range(self.max_tags)]
        tile_stat       = [[[Counter() for x in range(self.manycore_dim_x)] for y in range(self.manycore_dim_y)] for tag in range(self.max_tags)]

        tile_group_stat_start = [[Counter() for tg_id in range(self.max_tile_groups)] for tag in range(self.max_tags)]
        tile_group_stat_end   = [[Counter() for tg_id in range(self.max_tile_groups)] for tag in range(self.max_tags)]
        tile_group_stat       = [[Counter() for tg_id in range(self.max_tile_groups)] for tag in range(self.max_tags)]

        tag_credits = [[[0 for x in range(self.manycore_dim_x)] for y in range(self.manycore_dim_y)] for tag in range(self.max_tags)]


        for trace in traces:
            y = trace["y"]
            x = trace["x"]
            relative_y = y - self._BSG_ORIGIN_Y
            relative_x = x - self._BSG_ORIGIN_X

            # instantiate a CudaStatTag object with the tag value
            cst = CudaStatTag(trace["tag"])

            # extract tile group id from the print stat's tag value 
            # see CudaStatTag class comments for more detail

            # Separate depending on stat type (start or end)
            if(cst.isStart):
                tag_credits[cst.tag][relative_y][relative_x] += 1;
                # Only increase number of tags if haven't seen a trace from this tag before 
                if (not tile_group_stat_start[cst.tag]):
                    num_tags += 1
                # Only increase number of tile groups if haven't seen a trace from this tile group before
                if(not tile_group_stat_start[cst.tag][cst.tg_id]):
                    num_tile_groups[cst.tag] += 1
                for op in self.all_ops:
                    tile_stat_start[cst.tag][relative_y][relative_x][op] = trace[op]
                    tile_group_stat_start[cst.tag][cst.tg_id][op] += trace[op]

            elif (cst.isEnd):
                tag_credits[cst.tag][relative_y][relative_x] -= 1;
                if(tag_credits[cst.tag][relative_y][relative_x] < 0):
                    print ("Warning: missing start stat for tag {}, tile {},{}.".format(cst.tag, relative_x, relative_y))

                for op in self.all_ops:
                    tile_stat_end[cst.tag][relative_y][relative_x][op] = trace[op]
                    tile_group_stat_end[cst.tag][cst.tg_id][op] += trace[op]



        # Generate all tile stats by subtracting start time from end time
        for tag in range(self.max_tags):
            for y in range(self.manycore_dim_y):
                for x in range(self.manycore_dim_x):
                    if(tag_credits[tag][y][x] > 0):
                        print ("Warning: {} missing end stat(s) for tag {}, tile {},{}.".format(tag_credits[tag][y][x], tag, x, y))

                    tile_stat[tag][y][x] = tile_stat_end[tag][y][x] - tile_stat_start[tag][y][x]

        # Generate all tile group stats by subtracting start time from end time
        for tag in range(self.max_tags):
            for tg_id in range(num_tile_groups[tag]):
                tile_group_stat[tag][tg_id] = tile_group_stat_end[tag][tg_id] - tile_group_stat_start[tag][tg_id]

        # Generate total stats for each tile by summing all stats 
        for tag in range(self.max_tags):
            for y in range(self.manycore_dim_y):
                for x in range(self.manycore_dim_x):
                    for instr in self.instrs:
                        tile_stat[tag][y][x]["instr_total"] += tile_stat[tag][y][x][instr]
                    for stall in self.stalls:
                        tile_stat[tag][y][x]["stall_total"] += tile_stat[tag][y][x][stall]
                    for miss in self.misses:
                        tile_stat[tag][y][x]["miss_total"] += tile_stat[tag][y][x][miss]

        # Generate total stats for each tile group by summing all stats 
        for tag in range(self.max_tags):
            for tg_id in range(num_tile_groups[tag]):
                for instr in self.instrs:
                    tile_group_stat[tag][tg_id]["instr_total"] += tile_group_stat[tag][tg_id][instr]
                for stall in self.stalls:
                    tile_group_stat[tag][tg_id]["stall_total"] += tile_group_stat[tag][tg_id][stall]
                for miss in self.misses:
                    tile_group_stat[tag][tg_id]["miss_total"] += tile_group_stat[tag][tg_id][miss]

        self.instrs += ["instr_total"]
        self.stalls += ["stall_total"]
        self.misses += ["miss_total"]
        self.all_ops += ["instr_total", "stall_total", "miss_total"]

        return num_tags, num_tile_groups, tile_group_stat, tile_stat



    # Generate a stats dictionary for each tile containing the stat and it's aggregate count
    # other than timing, tile stats are only read once per tile from the end of file
    # i.e. if mesh dimensions are 4x4, only last 16 lines are needed 
    # Deprecated  -- might be used later if needed
    # This method count the aggregate stats (including the time tiles are waiting
    # for a program to be loaded)
    def __generate_inclusive_tile_stat (self, traces):
        tile_stat = [[Counter() for x in range(self.manycore_dim_x)] for y in range(self.manycore_dim_y)]
        trace_idx = len(traces)
        for y in range(self.manycore_dim_y):
            for x in range(self.manycore_dim_x):
                trace_idx -= 1
                trace = traces[trace_idx]
                for op in self.all_ops:
                    tile_stat[y][x][op] = trace[op]
        return tile_stat



    # Calculate aggregate manycore stats dictionary by summing 
    # all per tile stats dictionaries
    def __generate_manycore_stats_all(self, tile_stat):
        # Create a dictionary and initialize elements to zero
        manycore_stat = [Counter() for tag in range(self.max_tags)]
        for tag in range(self.max_tags):
            for y in range(self.manycore_dim_y):
                for x in range(self.manycore_dim_x):
                    for op in self.all_ops:
                        manycore_stat[tag][op] += tile_stat[tag][y][x][op]

        return manycore_stat
 


    # Parses stat file's header to generate list of all 
    # operations based on type (stat, instruction, miss, stall)
    def parse_header(self, f):
        # Generate lists of stats/instruction/miss/stall names
        instrs = []
        misses = []
        stalls = []
        stats  = []

        with open(f) as fp:
            rdr = csv.DictReader (fp, delimiter=",")
      
            header = rdr.fieldnames
            for item in header:
                if (item.startswith('instr_')):
                    if (not item == 'instr_total'):
                        instrs += [item]
                elif (item.startswith('miss_')):
                    misses += [item]
                elif (item.startswith('stall_')):
                    stalls += [item]
                else:
                    stats += [item]
        return (stats, instrs, misses, stalls)


# parses input arguments
def parse_args():
    parser = argparse.ArgumentParser(description="Vanilla Stats Parser")
    parser.add_argument("--input", default="vanilla_stats.csv", type=str,
                        help="Vanilla stats log file")
    parser.add_argument("--tile", default=False, action='store_true',
                        help="Also generate separate stats files for each tile.")
    parser.add_argument("--tile_group", default=False, action='store_true',
                        help="Also generate separate stats files for each tile group.")
    parser.add_argument("--dim-y", required=1, type=int,
                        help="Manycore Y dimension")
    parser.add_argument("--dim-x", required=1, type=int,
                        help="Manycore X dimension")
    args = parser.parse_args()
    return args


# main()
if __name__ == "__main__":
    np.seterr(divide='ignore', invalid='ignore')
    args = parse_args()
  
    st = VanillaStatsParser(args.dim_y, args.dim_x, args.tile, args.tile_group, args.input)
    st.print_manycore_stats_all()
    if(st.per_tile_stat):
        st.print_per_tile_stats_all()
    if(st.per_tile_group_stat):
        st.print_per_tile_group_stats_all()

  

