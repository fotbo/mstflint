# Copyright (C) Jan 2020 Mellanox Technologies Ltd. All rights reserved.
# Copyright (c) 2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# This software is available to you under a choice of one of two
# licenses.  You may choose to be licensed under the terms of the GNU
# General Public License (GPL) Version 2, available from the file
# COPYING in the main directory of this source tree, or the
# OpenIB.org BSD license below:
#
#     Redistribution and use in source and binary forms, with or
#     without modification, are permitted provided that the following
#     conditions are met:
#
#      - Redistributions of source code must retain the above
#        copyright notice, this list of conditions and the following
#        disclaimer.
#
#      - Redistributions in binary form must reproduce the above
#        copyright notice, this list of conditions and the following
#        disclaimer in the documentation and/or other materials
#        provided with the distribution.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.
# --


#######################################################
#
# AdbParser.py
# Python implementation of the Class AdbParser
# Created on:      23-Mar-2020 8:00:00 AM
# Original author: talve
#
#######################################################
import xml.etree.ElementTree as ET
import utils.constants as CONST
from utils.Exceptions import ResourceParseException
import ast


class AdbParser:
    """This class is responsible for loading and parsing the adb file.
    """
    def __init__(self, adb_path):
        try:
            # build a dictionary with all the adb nodes.
            self.adb_dict = ET.parse(adb_path)
            self._build_xml_elements_dict()
            self._build_definitions()
            # build a dictionary with only the adb nodes that contain segment id field.
            self.segment_id_nodes_dict = {}
            self._build_nodes_with_seg_id()
            # fix the offset since subnode should contain the offset with the addition of his parent.
            self._fix_nodes_offset(self.segment_id_nodes_dict)
            # update the union dict in the layout item if it has union_selector attribute
            self._update_union_dict()
            self._update_layout_items_paths()
            self._update_fields_with_conditions()
        except ResourceParseException as rpe:
            raise ResourceParseException("{0}\nFailed to parse the ADB file.".format(rpe))

    def _update_union(self, layout_item):
        """This method go over all the subitems and perform union dict updating
            to all items recursively.
        """
        try:
            if layout_item.uSelector and layout_item.uSelector.selector_full_path:
                selector_field, full_path, offset, size = self._get_explicit_field_details(layout_item.parent, layout_item.uSelector.selector_full_path)
                layout_item.uSelector.set_union_properties(self._get_enum_dict_from_field(selector_field), full_path, offset, size)
            for item in layout_item.subItems:
                self._update_union(item)
        except ResourceParseException as rpe:
            raise ResourceParseException("{0}\nFailed to update unions.".format(rpe))

    def _update_union_dict(self):
        """This method go over all the segments in the dictionary and
        send them to union dict update.
        """
        for seg in self.segment_id_nodes_dict.values():
            self._update_union(seg)

    def check_if_exists(self, operand, item, seg):
        founded_item = None
        if('.' not in operand or "$(parent)" in operand):
            if("$(parent)" in operand):
                operand = ''.join(operand.split("$(parent)."))
            for item_it in item.parent.subItems:
                if(item_it.name == operand):
                    return item_it
        if("$(segment)" in operand or '.' in operand):
            if("$(segment)" in operand):
                operand = operand.replace("$(segment)", seg.name)
            for item_it in seg.subItems:
                if(item_it.full_path == operand):
                    return item_it
                founded_item = self.check_if_exists(operand, item, item_it)

        return founded_item

    def update_fields_elements(self, item, parent, seg):
        """This method go over all the fields in parentment and update the condtion field"""
        if(item.condition and item.parent.is_conditional):
            for operand in item.condition.operands:
                founded_item = self.check_if_exists(operand, item, seg)
                if(founded_item):
                    item.condition.var_details[founded_item.name] = ConditionVariable()
                    item.condition.var_details[founded_item.name].is_found = True
                    item.condition.var_details[founded_item.name].size = founded_item.size
                    item.condition.var_details[founded_item.name].offset = founded_item.offset
                    item.condition.var_details[founded_item.name].substring = operand
        for item_it in item.subItems:
            self.update_fields_elements(item_it, item, seg)

    def _update_fields_with_conditions(self):
        """This method go over all the segments and update fields with conditions."""

        for seg in self.segment_id_nodes_dict.values():
            for item in seg.subItems:
                self.update_fields_elements(item, seg, seg)

    def _update_layout_items_paths(self):
        for seg in self.segment_id_nodes_dict.values():
            for item in seg.subItems:
                item.full_path = seg.name + '.' + item.name
                self.update_fields_paths(item, seg)

    def update_fields_paths(self, item, parent):
        """This method go over all the fields in segment and update the path field"""

        for item_it in parent.subItems:
            if(item_it.name not in item_it.full_path and parent.name):
                item_it.full_path += parent.name + "." + item_it.name
                if(item_it.parent.full_path):
                    item_it.full_path = item_it.parent.full_path + '.' + item_it.name
            self.update_fields_paths(item_it, item)

    def _create_enum_dict(self, enum_lst): # self, enum_lst: list
        """This function gets a list of enums and export them to a dictionary.
        if the enums_lst is empty, return None.
        """
        enums_dict = {}
        if len(enum_lst) <= 1:
            return None

        for enum in enum_lst:
            enum = enum.split('=')
            if len(enum) != 2 or enum[1] == '':
                raise ResourceParseException("Error - Failed to parse enum attribute '{0}'".format(str(enum)))
            enums_dict[int(enum[1], 0)] = enum[0]
        return enums_dict

    def _get_enum_dict_from_field(self, selector_field): # self, selector_field: ET.Element
        """This method gets field that contains enums for union_selector and build a dictionary with
        it's values and enums.
        """
        # in case the enum was already parsed - we should always get in this if
        if selector_field.enum_dict:
            return selector_field.enum_dict

        if selector_field.attrs['enum'] == '':
            raise ResourceParseException("Error - enum attribute for field '{0}' is empty!".format(selector_field.attrs['name']))

        enum_lst = selector_field.attrs['enum'].split(',')
        selector_dict = self._create_enum_dict(enum_lst)
        if selector_dict is None:
            raise ResourceParseException("Error - could not create the enums dictionary for field '{0}'".format(selector_field.attrs['name']))

        return selector_dict

    def _get_explicit_field_details(self, layout_item, relative_path): # self, layout_item, relative_path: str
        """This method gets a node's name and a relative path and returns the full path to the field, the
        explicit field to the enum related to the union selector and it's offset and size.
        """
        path = relative_path.split('.')
        if len(path) <= 1:
            raise ResourceParseException("Error - wrong relative path '{0}' for node '{1}'".format(relative_path, layout_item.name))
        full_path = path
        if path[0] in CONST.PARENT_LST:
            current_node = layout_item
            full_path[0] = layout_item.name
        else:
            current_node = self._retrieve_layout_item_by_name(path[0])
        try:
            if current_node is None:
                raise ResourceParseException("Failed to find node: '{0}'".format(current_node.name))
            for child in path[1:]:
                found_leaf = False
                for item in current_node.subItems:
                    if item.name == child:
                        current_node = item
                        found_leaf = True
                        break
                if current_node is None or not found_leaf:
                    raise ResourceParseException("Error - Failed to find field '{0}' in node '{1}', wrong path '{2}'".format(child, current_node.name, relative_path))
            return current_node, full_path, current_node.offset, current_node.size
        except ResourceParseException as rpe:
            raise ResourceParseException("{0}\nFailed to get the explicit field.".format(rpe))

    def _build_xml_elements_dict(self):
        self._node_xml_elements = {}
        for xml_element in self.adb_dict.iter('node'):
            self._node_xml_elements[xml_element.attrib["name"]] = xml_element

    def _build_definitions(self):
        # Create a list for 'inst_ifdef' (C like 'define' feature)
        self.ifdef_list = []
        for xml_element in self.adb_dict.iter('config'):
            if 'define' in xml_element.attrib and "=" not in xml_element.attrib["define"]:
                self.ifdef_list.append(xml_element.attrib["define"])

        # Create a dict for 'inst_if' (C like 'define' feature)
        self.if_dict = {}
        for xml_element in self.adb_dict.iter('config'):
            if 'define' in xml_element.attrib and "=" in xml_element.attrib["define"]:
                define_name, define_value = xml_element.attrib["define"].split("=")
                self.if_dict[define_name] = define_value

    def _check_condition(self, left_operand, right_operand, condition):
        """
        This method do the right logic according the given condition
        """
        if condition == ' EQ ':
            return left_operand == right_operand
        elif condition == ' LESS ':
            return left_operand < right_operand
        elif condition == ' LESS_EQ ':
            return left_operand <= right_operand
        elif condition == ' GREAT_EQ ':
            return left_operand >= right_operand
        elif condition == ' GREAT ':
            return left_operand > right_operand

        raise ResourceParseException("unsupported condition '{0}'".format(condition))

    def _get_condition_str(self, expression):
        """
        This method return the right condition exist in the expression
        """
        if ' EQ ' in expression:
            return ' EQ '
        elif ' LESS ' in expression:
            return ' LESS '
        elif ' LESS_EQ ' in expression:
            return ' LESS_EQ '
        elif ' GREAT_EQ ' in expression:
            return ' GREAT_EQ '
        elif ' GREAT ' in expression:
            return ' GREAT '

        raise ResourceParseException("No condition found in expression '{0}'".format(expression))

    def _check_single_expression(self, expression):
        """
        The method check the condition type and perform the
        logic accordingly.
        """
        condition = self._get_condition_str(expression)
        define_name, define_value = expression.split(condition)
        define_name = define_name.strip()
        define_value = define_value.strip()

        # if operand not exist in the if dictionary its a value that we need to convert from str to int
        left_operand = int(self.if_dict[define_name]) if define_name in self.if_dict else int(define_name)
        right_operand = int(self.if_dict[define_value]) if define_value in self.if_dict else int(define_value)
        return self._check_condition(left_operand, right_operand, condition)

    def _check_expressions(self, inst_if_attrib):
        """
        The method checks an expression and return True/False.
        Note that the method support only OR(s) or AND(s) but not together.
        """
        # assumption using 'OR' or 'AND' but not together
        expressions = []
        if ' OR ' in inst_if_attrib:
            expressions = inst_if_attrib.split(' OR ')
            # check if one of the expressions is True return True
            return any([self._check_single_expression(expression) for expression in expressions])
        elif ' AND ' in inst_if_attrib:
            expressions = inst_if_attrib.split(' AND ')
            # check if all of the expressions is True return True
            return all([self._check_single_expression(expression) for expression in expressions])

        # if only one expression
        return self._check_single_expression(inst_if_attrib)

    def _fix_nodes_offset(self, nodes_dict):
        """This method go over all the nodes in the dictionary and
        send them to offset fixing.
        """
        for node in nodes_dict.values():
            self._fix_node(node, node.offset)

    def _fix_node(self, node, offset):
        """This method go over all the subnodes and perform offset addition
        to all nodes recursively.
        """
        for sub_node in node.subItems:
            sub_node.offset += offset
            self._fix_node(sub_node, sub_node.offset)

    def _build_nodes_with_seg_id(self):
        """This method go over all the nodes in the adb dictionary and build a dictionary with the nodes
        that contain the "segment_id' field.
        """
        for node in self.adb_dict.iter('node'):
            if "segment_id" in node.attrib:
                adb_layout_item = self._node_to_AdbLayoutItem(node)
                self.segment_id_nodes_dict[node.attrib["segment_id"]] = adb_layout_item
                if("is_conditional" in node.attrib):
                    if(node.attrib["is_conditional"] == '1'):
                        self.segment_id_nodes_dict[node.attrib["segment_id"]].is_conditional = True

    def _retrieve_node_by_name(self, node_name): # self, node_name: str
        """This method return the node with the
        wanted name, if not found return None.
        """
        if node_name in self._node_xml_elements:
            return self._node_xml_elements[node_name]
        else:  # Missing-nodes feature
            return None

    def _retrieve_layout_item_by_name(self, name):
        """This method return the layout item with the wanted name,
        if it wasn't found returns None
        """
        for seg in self.segment_id_nodes_dict.values():
            if seg.name == name:
                return seg
            for subItem in seg.subItems:
                if subItem.name == name:
                    return subItem
        return None

    def _build_subitems(self, node, parent): # self, node: ET.Element, parent
        """This method build the subitems of the specific node.
        :param node: always field element except from the root which is node.
        """
        sub_items = []
        counter = 0
        last_elem_attr = [elem for elem in node.iter('field')][-1].attrib
        last_elem_flag = False

        for item in node:
            # Skip on elements that are excluded in the configuration ('inst_ifdef')
            if 'inst_ifdef' in item.attrib:
                if item.attrib['inst_ifdef'] not in self.ifdef_list:
                    continue
            # Skip on elements that are excluded in the configuration ('inst_if')
            if 'inst_if' in item.attrib:
                if not self._check_expressions(item.attrib['inst_if']):
                    continue
            if(last_elem_attr == item.attrib):
                last_elem_flag = True
            if "low_bound" in item.attrib and "high_bound" in item.attrib:
                counter += self._extract_array_to_list(node, counter, item, sub_items, parent, last_elem_flag)
            else:
                adb_layout_item = self._node_to_AdbLayoutItem(item, last_elem_flag)
                if adb_layout_item:
                    adb_layout_item.parent = parent
                    adb_layout_item.attrs = item.attrib
                    if 'union_selector' in item.attrib:
                        adb_layout_item.uSelector = AdbUnionSelector()
                        adb_layout_item.uSelector.selector_full_path = item.attrib['union_selector']
                    if 'enum' in item.attrib:
                        if item.attrib['enum'] == '':
                            enums_dict = None
                        else:
                            enums_lst = item.attrib['enum'].split(',')
                            enums_dict = self._create_enum_dict(enums_lst)
                            if enums_dict is None:
                                raise ResourceParseException("Error - could not create the enums dictionary for node '{0}'".format(item.attrib['name']))
                        adb_layout_item.enum_dict = enums_dict
                    sub_items.insert(counter, adb_layout_item)
                    counter += 1
        return sub_items

    def _extract_array_to_list(self, node, index, item, subitems_list, parent, last_elem_flag=False): # self, node: ET.Element, index, item, subitems_list, parent, last_elem_flag=False
        """This method build insert array items to a list form a specific index.
        :param node: always field element except from the root which is node.
        :param parent: layout item representing the parent of the array.
        """
        name = item.attrib["name"]
        start_index = int(item.attrib["low_bound"], 10)
        if item.attrib["high_bound"] == "VARIABLE":
            end_index = 1
        else:
            end_index = int(item.attrib["high_bound"], 10) + 1

        offset = self._parse_node_size(item.attrib["offset"])
        size = self._parse_node_size(item.attrib["size"])

        calculated_size = int(size / (end_index - start_index))
        current_offset = offset

        enum_dict = None
        if 'index_enum' in item.attrib:
            if item.attrib['index_enum'] == '':
                enum_dict = None
            else:
                enums_lst = item.attrib['index_enum'].split(',')
                enum_dict = self._create_enum_dict(enums_lst)
                if enum_dict is None:
                    raise ResourceParseException("Error - could not parse the 'index_enum' attribute for node '{0}'.".format(name))

        for i in range(start_index, end_index):
            adb_layout_item = AdbLayoutItem()
            # adb_layout_item.attrs = node.attrib
            adb_layout_item.parent = parent
            adb_layout_item.parent.size = self._parse_node_size(node.attrib["size"])
            adb_layout_item.nodeDesc = self._node_to_node_desc(item)
            if enum_dict is None or i not in enum_dict:
                adb_layout_item.name = name + "[" + str(i) + "]"
            else:
                adb_layout_item.name = name + "[" + enum_dict[i] + "]"
            adb_layout_item.size = calculated_size
            adb_layout_item.offset = current_offset
            if(last_elem_flag):
                adb_layout_item.last_elem = True
            if(item.attrib['high_bound'] == 'VARIABLE' and last_elem_flag and (parent.last_elem or 'segment_id' in node.attrib)):
                adb_layout_item.is_unlimited_array = True
            try:
                if "subnode" in item.attrib:
                    sub_node = self._retrieve_node_by_name(item.attrib["subnode"])
                    if sub_node:
                        if "attr_is_union" in sub_node.attrib:
                            adb_layout_item.nodeDesc.isUnion = self._parse_union(sub_node.attrib["attr_is_union"])
                        adb_layout_item.subItems = self._build_subitems(sub_node, parent)  # List of the child items (for nodes only)
            except ResourceParseException as rpe:
                raise ResourceParseException("{0}\nFailed to extract array to list from node '{1}'.".format(rpe, node.attrib['name']))

            subitems_list.insert(index, adb_layout_item)
            current_offset += calculated_size
            index += 1
        return index

    def _node_to_AdbLayoutItem(self, node, last_elem_flag=False): # self, node: ET.Element, last_elem_flag=False
        """This method build adb layout field from a given node.
        :param node: always field element except from the root which is node.
        """
        adb_layout_item = AdbLayoutItem()
        # adb_layout_item.fieldDesc = None  # Reference to "AdbFieldDesc" object, always != None
        # adb_layout_item.parent = None  # Reference to parent "AdbLayoutItem" object, always != None (expect of the root)
        adb_layout_item.name = node.attrib["name"]
        adb_layout_item.attrs = node.attrib
        if(last_elem_flag):
            adb_layout_item.last_elem = True
        if("condition" in node.attrib and node.attrib["condition"]):
            for conf in self.if_dict:
                for op in ConditionParser.operators:
                    if(conf in node.attrib["condition"] and (op + conf or conf + op) in node.attrib["condition"] and
                       not ("." + conf or conf + ".") in node.attrib["condition"]):
                        node.attrib["condition"] = node.attrib["condition"].replace(conf, self.if_dict[conf])
            adb_layout_item.condition = ConditionParser(node.attrib["condition"])
        if("is_conditional" in node.attrib):
            if(node.attrib["is_conditional"] == '1'):
                adb_layout_item.is_conditional = True

        adb_layout_item.nodeDesc = self._node_to_node_desc(node)  # For leafs must be None
        try:
            if "subnode" in node.attrib:
                sub_node = self._retrieve_node_by_name(node.attrib["subnode"])
                if "attr_is_union" in sub_node.attrib:
                    adb_layout_item.nodeDesc.isUnion = self._parse_union(sub_node.attrib["attr_is_union"])
                adb_layout_item.subItems = self._build_subitems(sub_node, adb_layout_item)  # List of the child items (for nodes only)
            else:
                adb_layout_item.subItems = self._build_subitems(node, adb_layout_item)
        except ResourceParseException as rpe:
            raise ResourceParseException("{0}\nFailed to create a valid layout from node '{1}'.".format(rpe, node.attrib['name']))

        if "offset" in node.attrib:
            adb_layout_item.offset = self._parse_node_size(node.attrib["offset"])

        adb_layout_item.size = self._parse_node_size(node.attrib["size"])
        adb_layout_item.attrs = {}  # Attributes after evaluations and array expanding
        adb_layout_item.arrayIdx = -1  # in case of being part of an array this will hold the array index, -1 means not part of array
        # adb_layout_item.vars = {}  # all variable relevant to this item after evaluation
        return adb_layout_item

    def _node_to_node_desc(self, node): # self, node: ET.Element
        """This method build adb desc field from a given node.
        """
        node_description = AdbNodeDesc()
        #node_description.name = ""
        # node_description.size = 0  # Size in bits
        if "attr_is_union" in node.attrib:
            node_description.isUnion = self._parse_union(node.attrib["attr_is_union"])
        #node_description.desc = ""
        # node_description.fields = []  # List of "AdbFieldDesc" objects
        # node_description.attrs = {}  # Dictionary of attributes: key, value
        # defined in
        #node_description.fileName = ""
        #node_description.lineNumber = -1
        return node_description

    def _parse_union(self, union_str):
        """This method parse the 'is union' attribute.
        """
        if union_str == "1":
            return True
        return False

    def _parse_node_size(self, size_str):
        """This method return the size of the 'size' attribute in bits.
        """
        hex_size = 0
        bit_size = 0
        size_parts = size_str.split(".")

        if len(size_parts) == 1:
            size_parts = size_parts[0], "0"
        if size_parts[0] == "":
            size_parts[0] = "0"

        hex_size = int(size_parts[0], 16) * 8
        bit_size = int(size_parts[1], 10)

        return hex_size + bit_size

####################################################################


class ConditionVariable(object):
    def __init__(self):
        self.is_found = False
        self.value = 0
        self.offset = 0
        self.size = 0
        self.substring = ""


class ConditionParser(object):
    ops = {'~': 'not ', 'AND': 'and', '|': 'or'}
    operators = ['~', '|', 'AND', '==', '!=', '(', ')']

    def __init__(self, condition):
        self.operands = set()
        self.parsed_str = self.parse_str(condition)
        self.var_details = {}
        self.result = False

    def parse_str(self, str):
        lst = str.split(" ")
        for elem in lst:
            tmp_elem = elem
            for op in ConditionParser.operators:
                if op in elem:
                    if op == '==' or op == '!=':
                        tmp_elem = elem.split(op)[0]
                    tmp_elem = tmp_elem.replace(op, "")
            if("segment" in tmp_elem):
                tmp_elem = tmp_elem.replace("segment", "(segment)")
            if("parent" in tmp_elem):
                tmp_elem = tmp_elem.replace("parent", "(parent)")
            self.operands.add(tmp_elem)
        if('' in self.operands):
            self.operands.remove('')
        for key in ConditionParser.ops:
            if key in str:
                str = str.replace(key, ConditionParser.ops[key])
        return str

    def eval_expr(self):
        try:
            tree = ast.parse(self.parsed_str, mode='eval')
        except SyntaxError:
            return    # not a Python expression
        if not all(isinstance(node, (ast.Expression,
                                     ast.UnaryOp, ast.unaryop,
                                     ast.BinOp, ast.operator,
                                     ast.BoolOp, ast.Num, ast.And, ast.Or,
                                     ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for node in ast.walk(tree)):
            return    # not a mathematical expression (numbers and op)
        return eval(compile(tree, filename='', mode='eval'))


class AdbLayoutItem(object):
    """ Represents a Layout node/leaf instance """

    ##########################
    def __init__(self):
        self.fieldDesc = None        # Reference to "AdbFieldDesc" object, always != None
        self.nodeDesc = None         # For leafs must be None
        self.parent = None           # Reference to parent "AdbLayoutItem" object, always != None (expect of the root)
        self.name = ""               # The instance name
        self.subItems = []           # List of the child items (for nodes only)
        self.offset = 0              # Global offset (relative to the 0 address)
        self.size = 0                # Element size in bits
        self.attrs = {}              # Attributes after evaluations and array expanding
        self.arrayIdx = -1           # in case of being part of an array this will hold the array index, -1 means not part of array
        self.vars = {}               # all variable relevant to this item after evaluation
        self.uSelector = None        # data structure that represent the union-selector properties
        self.enum_dict = None        # in case of being an enum element, holds the key-value of the enum
        self.condition = None
        self.full_path = ""
        self.is_conditional = False
        self.last_elem = False
        self.is_unlimited_array = False


####################################################################
class AdbFieldDesc(object):
    """ Represents an ADABE field descriptor, objects of this type are immutable """

    ##########################
    def __init__(self):
        self.name = ""
        self.size = 0                    # Size in bits (the whole array size)
        self.offset = None                 # Offset in bits (relative to the node start addr)
        self.desc = ""
        self.lowBound = 0
        self.highBound = 0
        self.unlimitedArr = False
        self.definedAsArr = False
        self.subNode = None                # Subnode name as string
        self.attrs = {}                  # Dictionary of attributes: key, value
        self.reserved = False
        self.condition = ""


####################################################################
class AdbNodeDesc(object):
    """ Represents an ADABE node descriptor, objects of this type are immutable """

    ##########################
    def __init__(self):
        self.name = ""
        self.size = 0                 # Size in bits
        self.isUnion = False
        self.desc = ""
        self.fields = []                # List of "AdbFieldDesc" objects
        self.attrs = {}                # Dictionary of attributes: key, value

        # defined in
        self.fileName = ""
        self.lineNumber = -1


####################################################################
class AdbUnionSelector(object):
    """ Represents an ADABE union-selector descriptor, objects of this type are immutable """

    ##########################
    def __init__(self):
        self.dict = None               # mapping between the value and the enum, None if empty
        self.full_path = None          # full path to the selector field, None if empty
        self.offset = None             # absolute offset from the begining of the segment until the selector field (by layout)
        self.size = None               # size of the selector itself

    def set_union_properties(self, union_dict, full_path, offset, size):
        self.dict = union_dict
        self.full_path = full_path
        self.offset = offset
        self.size = size
