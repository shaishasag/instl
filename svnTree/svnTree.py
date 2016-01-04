#!/usr/bin/env python
from __future__ import print_function

import os
import re
from collections import OrderedDict

import yaml

import svnItem
import utils
import aYaml


comment_line_re = re.compile(r"""
            ^
            \s*\#\s*
            (?P<the_comment>.*)
            $
            """, re.X)

map_info_extension_to_format = {"txt": "text", "text": "text",
                                "inf": "info", "info": "info",
                                "yml": "yaml", "yaml": "yaml",
                                "pick": "pickle", "pickl": "pickle", "pickle": "pickle",
                                "props": "props", "prop": "props",
                                "file-sizes": "file-sizes"
}


class SVNTree(svnItem.SVNTopItem):
    """ SVNTree inherits from SVNTopItem and adds the functionality
        of reading and writing itself in various text formats:
            info: produced by SVN's info command (read only)
            props: produced by SVN's proplist command (read only)
            text: SVNItem's native format (read and write)
            yaml: yaml... (read and write)
    """

    def __init__(self):
        """ Initializes a SVNTree object """
        super(SVNTree, self).__init__()
        self.read_func_by_format = {"info": self.read_from_svn_info,
                                    "text": self.read_from_text,
                                    "yaml": self.pseudo_read_from_yaml,
                                    "props": self.read_props,
                                    "file-sizes": self.read_file_sizes
                                    }

        self.write_func_by_format = {"text": self.write_as_text,
                                     "yaml": self.write_as_yaml,
        }
        self.path_to_file = None
        self.comments = list()

    def valid_read_formats(self):
        """ returns a list of file formats that can be read by SVNTree """
        return list(self.read_func_by_format.keys())

    @utils.timing
    def read_info_map_from_file(self, in_file, a_format="guess"):
        """ Reads from file. All previous sub items are cleared
            before reading, unless the a_format is 'props' in which case
            the properties are added to existing sub items.
            raises ValueError is a_format is not supported.
        """
        self.path_to_file = in_file
        if a_format == "guess":
            _, extension = os.path.splitext(self.path_to_file)
            a_format = map_info_extension_to_format[extension[1:]]
        self.comments.append("Original file " + self.path_to_file)
        if a_format in list(self.read_func_by_format.keys()):
            with utils.open_for_read_file_or_url(self.path_to_file) as rfd:
                if a_format not in ("props", "file-sizes"):
                    self.clear_subs()
                self.read_func_by_format[a_format](rfd)
        else:
            raise ValueError("Unknown read a_format " + a_format)

    def read_from_svn_info(self, rfd):
        """ reads new items from svn info items prepared by iter_svn_info """
        for item_dict in self.iter_svn_info(rfd):
            self.new_item_at_path(item_dict['path'], item_dict)

    def read_from_text(self, rfd):
        for line in rfd:
            match = comment_line_re.match(line)
            if match:
                self.comments.append(match.group("the_comment"))
            else:
                self.new_item_from_str_re(line)

    def read_from_yaml(self, rfd):
        try:
            for a_node in yaml.compose_all(rfd):
                self.read_yaml_node(a_node)
        except yaml.YAMLError as ye:
            raise utils.InstlException(" ".join(("YAML error while reading file", "'" + rfd.name + "':\n", str(ye))), ye)
        except IOError as ioe:
            raise utils.InstlException(" ".join(("Failed to read file", "'" + rfd.name + "'", ":")), ioe)

    def pseudo_read_from_yaml(self, rfd):
        """ read from yaml file without the yaml parser - much faster
            but might break is the format changes.
        """
        yaml_line_re = re.compile("""
                    ^
                    (?P<indent>\s*)
                    (?P<path>[^:]+)
                    :\s
                    (?P<props>
                    (?P<flags>[dfsx]+)
                    \s
                    (?P<revision>\d+)
                    (\s
                    (?P<checksum>[\da-f]+))?
                    )?
                    $
                    """, re.X)
        try:
            line_num = 0
            indent = -1  # so indent of first line (0) > indent (-1)
            spaces_per_indent = 4
            path_parts = list()
            for line in rfd:
                line_num += 1
                match = yaml_line_re.match(line)
                if match:
                    new_indent = len(match.group('indent')) / spaces_per_indent
                    if match.group('path') != "_p_":
                        how_much_to_pop = indent - new_indent + 1
                        if how_much_to_pop > 0:
                            path_parts = path_parts[0: -how_much_to_pop]
                        path_parts.append(match.group('path'))
                        if match.group('props'):  # it's a file
                            # print(((new_indent * spaces_per_indent)-1) * " ", "/".join(path_parts), match.group('props'))
                            self.new_item_at_path(path_parts, {'flags': match.group('flags'), 'revision': match.group('revision')})
                        indent = new_indent
                    else:  # previous element was a folder
                        # print(((new_indent * spaces_per_indent)-1) * " ", "/".join(path_parts), match.group('props'))
                        self.new_item_at_path(path_parts, {'flags': match.group('flags'), 'revision': match.group('revision')})
                else:
                    if indent != -1:  # first lines might be empty
                        ValueError("no match at line " + str(line_num) + ": " + line)
        except Exception as unused_ex:
            print("exception at line:", line_num, line)
            raise

    def read_props(self, rfd):
        props_line_re = re.compile("""
                    ^
                    (
                    Properties\son\s
                    '
                    (?P<path>[^:]+)
                    ':
                    )
                    |
                    (
                    \s+
                    svn:
                    (?P<prop_name>[\w\-_]+)
                    )
                    $
                    """, re.X)
        line_num = 0
        try:
            prop_name_to_char = {'executable': 'x', 'special': 's'}
            item = None
            for line in rfd:
                line_num += 1
                match = props_line_re.match(line)
                if match:
                    if match.group('path'):
                        # get_item_at_path might return None for invalid paths, mainly '.'
                        item = self.get_item_at_path(match.group('path'))
                    elif match.group('prop_name'):
                        if item is not None:
                            prop_name = match.group('prop_name')
                            if prop_name in prop_name_to_char:
                                item.flags += prop_name_to_char[match.group('prop_name')]
                            else:
                                if not item.props:
                                    item.props = list()
                                item.props.append(prop_name)
                else:
                    ValueError("no match at file: " + rfd.name + ", line: " + str(line_num) + ": " + line)
        except Exception as ex:
            print("Line:", line_num, ex)
            raise

    def valid_write_formats(self):
        return list(self.write_func_by_format.keys())

    @utils.timing
    def write_to_file(self, in_file, in_format="guess", comments=True):
        """ pass in_file="stdout" to output to stdout.
            in_format is either text, yaml, pickle
        """
        self.path_to_file = in_file
        if in_format == "guess":
            _, extension = os.path.splitext(self.path_to_file)
            in_format = map_info_extension_to_format[extension[1:]]
        if in_format in list(self.write_func_by_format.keys()):
            with utils.write_to_file_or_stdout(self.path_to_file) as wfd:
                self.write_func_by_format[in_format](wfd, comments)
        else:
            raise ValueError("Unknown write in_format " + in_format)

    def write_as_text(self, wfd, comments=True):
        if comments and len(self.comments) > 0:
            for comment in self.comments:
                wfd.write("# " + comment + "\n")
            wfd.write("\n")
        for item in self.walk_items():
            wfd.write(str(item) + "\n")

    def write_as_yaml(self, wfd, comments=True):
        aYaml.writeAsYaml(self, out_stream=wfd, indentor=None, sort=True)

    def repr_for_yaml(self):
        """         writeAsYaml(svni1, out_stream=sys.stdout, indentor=None, sort=True)         """
        retVal = OrderedDict()
        for sub_name in sorted(self.subs.keys()):
            the_sub = self.subs[sub_name]
            if the_sub.isDir():
                retVal[the_sub.name] = the_sub.repr_for_yaml()
            else:
                ValueError("SVNTree does not support files in the top most directory")
        return retVal

    def iter_svn_info(self, long_info_fd):
        """ Go over the lines of the output of svn info command
            for each block describing a file or directory, yield
            a tuple formatted as (path, type, last changed revision).
            Where type is 'f' for file or 'd' for directory. """
        try:
            svn_info_line_re = re.compile("""
                        ^
                        (?P<key>Path|Last\ Changed\ Rev|Node\ Kind|Revision|Checksum|Tree\ conflict)
                        :\s*
                        (?P<rest_of_line>.*)
                        $
                        """, re.VERBOSE)

            def create_info_line_from_record(a_record):
                """ On rare occasions there is no 'Last Changed Rev' field, just 'Revision'.
                    So we use 'Revision' as 'Last Changed Rev'.
                """
                revision = a_record.get("Last Changed Rev", None)
                if revision is None:
                    revision = a_record.get("Revision", None)
                checksum = a_record.get("Checksum", None)
                return a_record["Path"], short_node_kind[a_record["Node Kind"]], int(revision), checksum

            def create_info_dict_from_record(a_record):
                """ On rare occasions there is no 'Last Changed Rev' field, just 'Revision'.
                    So we use 'Revision' as 'Last Changed Rev'.
                """
                retVal = dict()
                retVal['path']  =  a_record["Path"]
                retVal['flags'] = short_node_kind[a_record["Node Kind"]]
                if "Last Changed Rev" in a_record:
                    retVal['revision'] = a_record["Last Changed Rev"]
                elif "Revision" in a_record:
                    retVal['revision'] = a_record["Revision"]
                retVal['checksum'] = a_record.get("Checksum", None)

                return retVal

            short_node_kind = {"file": "f", "directory": "d"}
            record = dict()
            line_num = 0
            for line in long_info_fd:
                line_num += 1
                if line != "\n":
                    the_match = svn_info_line_re.match(line)
                    if the_match:
                        if the_match.group('key') == "Tree conflict":
                            raise ValueError(
                                " ".join(("Tree conflict at line", str(line_num), "Path:", record['Path'])))
                        record[the_match.group('key')] = the_match.group('rest_of_line')
                else:
                    if record and record["Path"] != ".":  # in case there were several empty lines between blocks
                        yield create_info_dict_from_record(record)
                    record.clear()
            if record and record["Path"] != ".":  # in case there was no extra line at the end of file
                yield create_info_dict_from_record(record)
        except KeyError as unused_ke:
            print(unused_ke)
            print("Error:", "line:", line_num, "record:", record)
            raise

    def initialize_from_folder(self, in_folder):
        prefix_len = len(in_folder)+1
        for root, dirs, files in os.walk(in_folder, followlinks=False):
            for a_file in files:
                if a_file != ".DS_Store": # temp hack, list of ignored files should be moved to a variable
                    relative_path = os.path.join(root, a_file)[prefix_len:]
                    self.new_item_at_path(relative_path, {'flags':"f", 'revision': 0, 'checksum': "0"}, create_folders=True)

    def read_file_sizes(self, rfd):
        for line in rfd:
            match = comment_line_re.match(line)
            if not match:
                parts = line.rstrip().split(", ", 2)
                item = self.get_item_at_path(parts[0])
                if item:
                    item.size = int(parts[1])
                else:
                    print(parts[0], "was not found")

class WtarFilter(object):
    """ WtarFilter is passed to SVNItem.walk_file_items_with_filter as the filter parameter
        to match files that end with .wtar, .wtar.aa,...
    """
    def __init__(self, base_name=r""".+"""):
        # Regex fo find files who's name starts with the source's name and have .wtar or wtar.aa... extension
        # NOT compiled with re.VERBOSE since the file name may contain spaces
        self.wtar_file_re = re.compile(base_name + r"""\.wtar(\...)?$""")

    def __call__(self, file_item):
        match = self.wtar_file_re.match(file_item.name)
        retVal = match is not None
        return retVal

if __name__ == "__main__":
    t = SVNTree()
    t.read_svn_info_file(sys.argv[1])
    # for item in t.walk_items():
    #    print(str(item))
