#!/usr/bin/env python3


import os
import sys
import re
import abc
import weakref
import platform
import appdirs
import urllib.error

import aYaml
import utils
from .batchAccumulator import BatchAccumulator
from .platformSpecificHelper_Base import PlatformSpecificHelperFactory

from configVar import value_ref_re
from configVar import var_stack
from configVar import ConfigVarYamlReader

from . import connectionBase
from db import DBMaster, get_db_url
from .indexItemTable import IndexItemsTable
from svnTree import SVNTable


def check_version_compatibility():
    retVal = True
    message = ""
    if "INSTL_MINIMAL_VERSION" in var_stack:
        cur_instl_ver = list(map(int, var_stack.ResolveVarToList("__INSTL_VERSION__")))
        required_instl_ver = list(map(int, var_stack.ResolveVarToList("INSTL_MINIMAL_VERSION")))
        retVal = cur_instl_ver >= required_instl_ver
        if not retVal:
            message = "instl version {} < minimal required version {}".format(cur_instl_ver, required_instl_ver)
    return retVal, message


# noinspection PyPep8Naming
class InstlInstanceBase(ConfigVarYamlReader, metaclass=abc.ABCMeta):
    """ Main object of instl. Keeps the state of variables and install index
        and knows how to create a batch file for installation. InstlInstanceBase
        must be inherited by platform specific implementations, such as InstlInstance_mac
        or InstlInstance_win.
    """
    db = None
    items_table = None
    info_map_table = None

    def __init__(self, initial_vars=None):
        self.need_items_table = False
        self.need_info_map_table = False

        ConfigVarYamlReader.__init__(self)

        search_paths_var = var_stack.get_configVar_obj("__SEARCH_PATHS__")
        self.path_searcher = utils.SearchPaths(search_paths_var)
        self.url_translator = connectionBase.translate_url
        self.init_default_vars(initial_vars)
        # noinspection PyUnresolvedReferences
        self.read_name_specific_defaults_file(super().__thisclass__.__name__)
        # initialize the search paths helper with the current directory and dir where instl is now
        self.path_searcher.add_search_path(os.getcwd())
        self.path_searcher.add_search_path(os.path.dirname(os.path.realpath(sys.argv[0])))
        self.path_searcher.add_search_path(var_stack.ResolveVarToStr("__INSTL_DATA_FOLDER__"))

        self.platform_helper = PlatformSpecificHelperFactory(var_stack.ResolveVarToStr("__CURRENT_OS__"), self)
        # init initial copy tool, tool might be later overridden after reading variable COPY_TOOL from yaml.
        self.platform_helper.init_copy_tool()

        self.batch_accum = BatchAccumulator()
        self.do_not_write_vars = ("INFO_MAP_SIG", "INDEX_SIG", "PUBLIC_KEY", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "__CREDENTIALS__")
        self.out_file_realpath = None
        self.internal_progress = 0  # progress of preparing installer NOT of the installation
        self.num_digits_repo_rev_hierarchy=None
        self.num_digits_per_folder_repo_rev_hierarchy=None

    def progress(self, message):
        self.internal_progress += 1
        print("""Progress {} of {}; {}""".format(self.internal_progress, 100, message), flush=True)

    def init_specific_doc_readers(self):
        ConfigVarYamlReader.init_specific_doc_readers(self)
        self.specific_doc_readers.pop("__no_tag__", None)
        self.specific_doc_readers.pop("__unknown_tag__", None)

        self.specific_doc_readers["!define"] = self.read_defines
        self.specific_doc_readers["!define_const"] = self.read_const_defines

        acceptables = var_stack.ResolveVarToList("ACCEPTABLE_YAML_DOC_TAGS", default=[])
        if "__INSTL_COMPILED__" in var_stack:
            if var_stack.ResolveVarToStr("__INSTL_COMPILED__") == "True":
                acceptables.append("define_Compiled")
            else:
                acceptables.append("define_Uncompiled")
        for acceptibul in acceptables:
            if acceptibul.startswith("define_if_not_exist"):
                self.specific_doc_readers["!" + acceptibul] = self.read_defines_if_not_exist
            elif acceptibul.startswith("define"):
                self.specific_doc_readers["!" + acceptibul] = self.read_defines

        self.specific_doc_readers["!index"] = self.read_index
        self.specific_doc_readers["!require"] = self.read_require

    def get_version_str(self):
        instl_ver_str = ".".join(var_stack.ResolveVarToList("__INSTL_VERSION__"))
        if "__PLATFORM_NODE__" not in var_stack:
            var_stack.update({"__PLATFORM_NODE__": platform.node()})
        retVal = var_stack.ResolveStrToStr(
            "$(INSTL_EXEC_DISPLAY_NAME) version "+instl_ver_str+" $(__COMPILATION_TIME__) $(__PLATFORM_NODE__)")
        return retVal

    def init_default_vars(self, initial_vars):
        if initial_vars:
            var_description = "from initial_vars"
            for var, value in initial_vars.items():
                if isinstance(value, str):
                    var_stack.add_const_config_variable(var, var_description, value)
                else:
                    var_stack.add_const_config_variable(var, var_description, *value)

        var_description = "from InstlInstanceBase.init_default_vars"

        # read defaults/main.yaml
        main_defaults_file_path = os.path.join(var_stack.ResolveVarToStr("__INSTL_DATA_FOLDER__"), "defaults", "main.yaml")
        self.read_yaml_file(main_defaults_file_path)

        # read defaults/compile-info.yaml
        compile_info_file_path = os.path.join(var_stack.ResolveVarToStr("__INSTL_DATA_FOLDER__"), "defaults", "compile-info.yaml")
        if os.path.isfile(compile_info_file_path):
            self.read_yaml_file(compile_info_file_path)
        if "__COMPILATION_TIME__" not in var_stack:
            if var_stack.ResolveVarToStr("__INSTL_COMPILED__") == "True":
                var_stack.add_const_config_variable("__COMPILATION_TIME__", var_description, "unknown compilation time")
            else:
                var_stack.add_const_config_variable("__COMPILATION_TIME__", var_description, "(not compiled)")

        self.read_user_config()

    def read_name_specific_defaults_file(self, file_name):
        """ read class specific file from defaults/class_name.yaml """
        name_specific_defaults_file_path = os.path.join(var_stack.ResolveVarToStr("__INSTL_DATA_FOLDER__"), "defaults",
                                                        file_name + ".yaml")
        if os.path.isfile(name_specific_defaults_file_path):
            self.read_yaml_file(name_specific_defaults_file_path)

    def read_user_config(self):
        user_config_path = var_stack.ResolveVarToStr("__USER_CONFIG_FILE_PATH__")
        if os.path.isfile(user_config_path):
            previous_allow_reading_of_internal_vars = self.allow_reading_of_internal_vars
            self.allow_reading_of_internal_vars = True
            self.read_yaml_file(user_config_path)
            self.allow_reading_of_internal_vars = previous_allow_reading_of_internal_vars

    def check_prerequisite_var_existence(self, prerequisite_vars):
        missing_vars = [var for var in prerequisite_vars if var not in var_stack]
        if len(missing_vars) > 0:
            msg = "Prerequisite variables were not defined: " + ", ".join(missing_vars)
            raise ValueError(msg)

    def init_from_cmd_line_options(self, cmd_line_options_obj):
        """ turn command line options into variables """
        const_attrib_to_var = {
            "input_file": ("__MAIN_INPUT_FILE__", None),
            "output_file": ("__MAIN_OUT_FILE__", None),
            "props_file": ("__PROPS_FILE__", None),
            "config_file": ("__CONFIG_FILE__", None),
            "sh1_checksum": ("__SHA1_CHECKSUM__", None),
            "rsa_signature": ("__RSA_SIGNATURE__", None),
            "just_with_number": ("__JUST_WITH_NUMBER__", "0"),
            "limit_command_to": ("__LIMIT_COMMAND_TO__", None),
            "shortcut_path": ("__SHORTCUT_PATH__", None),
            "target_path": ("__SHORTCUT_TARGET_PATH__", None),
            "credentials": ("__CREDENTIALS__", None),
            "base_url": ("__BASE_URL__", None),
            "file_sizes_file": ("__FILE_SIZES_FILE__", None),
            "output_format": ("__OUTPUT_FORMAT__", None),
        }

        default_remark = "from command line options"
        for attrib, var in const_attrib_to_var.items():
            attrib_value = getattr(cmd_line_options_obj, attrib)
            if attrib_value:
                var_stack.add_const_config_variable(var[0], default_remark, *attrib_value)
            elif var[1] is not None:  # there's a default
                var_stack.add_const_config_variable(var[0], "from default", var[1])

        non_const_attrib_to_var = {
            "target_repo_rev": "TARGET_REPO_REV",
            "base_repo_rev": "BASE_REPO_REV",
            "ls_format": "LS_FORMAT",
            "start_progress": "__START_DYNAMIC_PROGRESS__",
            "total_progress": "__TOTAL_DYNAMIC_PROGRESS__",
        }

        for attrib, var in non_const_attrib_to_var.items():
            attrib_value = getattr(cmd_line_options_obj, attrib)
            if attrib_value:
                var_stack.set_var(var, default_remark).append(attrib_value[0])

        if cmd_line_options_obj.command:
            self.the_command = cmd_line_options_obj.command
            self.fixed_command = self.the_command.replace('-', '_')
            var_stack.set_var("__MAIN_COMMAND__", default_remark).append(cmd_line_options_obj.command)

        if hasattr(cmd_line_options_obj, "subject") and cmd_line_options_obj.subject is not None:
            var_stack.add_const_config_variable("__HELP_SUBJECT__", default_remark,
                                                cmd_line_options_obj.subject)
        else:
            var_stack.add_const_config_variable("__HELP_SUBJECT__", default_remark, "")

        if cmd_line_options_obj.state_file:
            var_stack.add_const_config_variable("__MAIN_STATE_FILE__", default_remark,
                                                cmd_line_options_obj.state_file)

        if cmd_line_options_obj.run:
            var_stack.add_const_config_variable("__RUN_BATCH__", default_remark, "yes")

        if cmd_line_options_obj.no_wtar_artifacts:
            var_stack.add_const_config_variable("__NO_WTAR_ARTIFACTS__", default_remark, "yes")

        if cmd_line_options_obj.which_revision:
            var_stack.add_const_config_variable("__WHICH_REVISION__", default_remark, cmd_line_options_obj.which_revision[0])

        if cmd_line_options_obj.dock_item_path:
            var_stack.add_const_config_variable("__DOCK_ITEM_PATH__", default_remark, *cmd_line_options_obj.dock_item_path)
        if cmd_line_options_obj.dock_item_label:
            var_stack.add_const_config_variable("__DOCK_ITEM_LABEL__", default_remark, *cmd_line_options_obj.dock_item_label)
        if cmd_line_options_obj.remove_from_dock:
            var_stack.add_const_config_variable("__REMOVE_FROM_DOCK__", default_remark, "yes")
        if cmd_line_options_obj.restart_the_dock:
            var_stack.add_const_config_variable("__RESTART_THE_DOCK__", default_remark, "yes")
        if cmd_line_options_obj.fail_exit_code:
            var_stack.add_const_config_variable("__FAIL_EXIT_CODE__", default_remark, *cmd_line_options_obj.fail_exit_code)
        if cmd_line_options_obj.set_run_as_admin:
            var_stack.add_const_config_variable("__RUN_AS_ADMIN__", default_remark, "yes")
        if cmd_line_options_obj.only_installed:
            var_stack.add_const_config_variable("__REPORT_ONLY_INSTALLED__", default_remark, "yes")
        if var_stack.ResolveVarToStr("__CURRENT_OS__") == "Mac":
            if cmd_line_options_obj.parallel:
                var_stack.add_const_config_variable("__RUN_COMMAND_LIST_IN_PARALLEL__", default_remark, "yes")
        if cmd_line_options_obj.no_numbers_progress:
            var_stack.add_const_config_variable("__NO_NUMBERS_PROGRESS__", default_remark, "yes")

        if cmd_line_options_obj.define:
            individual_definitions = cmd_line_options_obj.define[0].split(",")
            for definition in individual_definitions:
                name, value = definition.split("=")
                var_stack.set_var(name, "from command line define option").append(value)

        if "__MAIN_OUT_FILE__" not in var_stack:
            default_out_file = self.get_default_out_file()
            if default_out_file:
                var_stack.add_const_config_variable("__MAIN_OUT_FILE__", "from write_batch_file",
                                                default_out_file)
        self.init_db()

    def init_db(self):
        if self.need_items_table or self.need_info_map_table:
            if not InstlInstanceBase.db:
                db_url = get_db_url(self.the_command)
                ddls_folder = os.path.join(var_stack.ResolveVarToStr("__INSTL_DATA_FOLDER__"), "defaults")
                if db_url != ":memory:":
                    utils.safe_remove_file(db_url)
                InstlInstanceBase.db = DBMaster(db_url, ddls_folder)
                var_stack.add_const_config_variable("__DATABASE_URL__", "", db_url)
            if self.need_items_table and not InstlInstanceBase.items_table:
                InstlInstanceBase.items_table = IndexItemsTable(InstlInstanceBase.db)
            if self.need_info_map_table and not InstlInstanceBase.info_map_table:
                InstlInstanceBase.info_map_table = SVNTable(InstlInstanceBase.db)

    def close(self):
        if InstlInstanceBase.info_map_table:
            del InstlInstanceBase.info_map_table
        if InstlInstanceBase.items_table:
            del InstlInstanceBase.items_table
        if InstlInstanceBase.db:
            InstlInstanceBase.db.close()
        var_stack.print_statistics()

    def get_default_out_file(self):
        retVal = None
        if "__MAIN_INPUT_FILE__" in var_stack:
            retVal = "$(__MAIN_INPUT_FILE__)-$(__MAIN_COMMAND__).$(BATCH_EXT)"
        return retVal

    def read_require(self, a_node, *args, **kwargs):
        del args
        self.items_table.read_require_node(a_node)

    def write_require_file(self, file_path, require_dict):
        with utils.utf8_open(file_path, "w") as wfd:
            utils.make_open_file_read_write_for_all(wfd)

            define_dict = aYaml.YamlDumpDocWrap({"REQUIRE_REPO_REV": var_stack.ResolveVarToStr("MAX_REPO_REV")},
                                                '!define', "definitions",
                                                 explicit_start=True, sort_mappings=True)
            require_dict = aYaml.YamlDumpDocWrap(require_dict, '!require', "requirements",
                                                 explicit_start=True, sort_mappings=True)

            aYaml.writeAsYaml((define_dict, require_dict), wfd)

    internal_identifier_re = re.compile("""
                                        __                  # dunder here
                                        (?P<internal_identifier>\w*)
                                        __                  # dunder there
                                        """, re.VERBOSE)

    def resolve_defined_paths(self):
        self.path_searcher.add_search_paths(var_stack.ResolveVarToList("SEARCH_PATHS", default=[]))
        for path_var_to_resolve in var_stack.ResolveVarToList("PATHS_TO_RESOLVE", default=[]):
            if path_var_to_resolve in var_stack:
                resolved_path = self.path_searcher.find_file(var_stack.ResolveVarToStr(path_var_to_resolve),
                                                             return_original_if_not_found=True)
                var_stack.set_var(path_var_to_resolve, "resolve_defined_paths").append(resolved_path)

    def provision_public_key_text(self):
        if "PUBLIC_KEY" not in var_stack:
            if "PUBLIC_KEY_FILE" in var_stack:
                public_key_file = var_stack.ResolveVarToStr("PUBLIC_KEY_FILE")
                with utils.open_for_read_file_or_url(public_key_file, connectionBase.translate_url, self.path_searcher) as open_file:
                    public_key_text = open_file.fd.read()
                    var_stack.set_var("PUBLIC_KEY", "from " + public_key_file).append(public_key_text)
            else:
                raise ValueError("No public key, variables PUBLIC_KEY & PUBLIC_KEY_FILE are not defined")
        resolved_public_key = var_stack.ResolveVarToStr("PUBLIC_KEY")
        return resolved_public_key

    def read_include_node(self, i_node, *args, **kwargs):
        if i_node.isScalar():
            resolved_file_name = var_stack.ResolveStrToStr(i_node.value)
            self.read_yaml_file(resolved_file_name, *args, **kwargs)
        elif i_node.isSequence():
            for sub_i_node in i_node:
                self.read_include_node(sub_i_node, *args, **kwargs)
        elif i_node.isMapping():
            if "url" in i_node:
                resolved_file_url = var_stack.ResolveStrToStr(i_node["url"].value)
                expected_checksum = None
                if "checksum" in i_node:
                    expected_checksum = var_stack.ResolveStrToStr(i_node["checksum"].value)

                file_destination = None
                if "destination" in i_node:
                    file_destination = var_stack.ResolveStrToStr(i_node["destination"].value)

                try:
                    file_path = utils.download_from_file_or_url(in_url=resolved_file_url,
                                                                in_target_path=file_destination,
                                                                translate_url_callback=connectionBase.translate_url,
                                                                cache_folder=self.get_default_sync_dir(continue_dir="cache", make_dir=True),
                                                                expected_checksum=expected_checksum)
                    self.read_yaml_file(file_path, *args, **kwargs)
                    if not file_destination:
                        file_name = utils.last_url_item(resolved_file_url)
                        destination = var_stack.ResolveStrToStr("$(LOCAL_REPO_REV_BOOKKEEPING_DIR)/{}".format(file_name))
                        utils.smart_copy_file(file_path, destination)
                except (FileNotFoundError, urllib.error.URLError):
                    ignore = kwargs.get('ignore_if_not_exist', False)
                    if ignore:
                        print("'ignore_if_not_exist' specified, ignoring FileNotFoundError for", resolved_file_url)
                    else:
                        raise

                if "copy" in i_node:
                    self.batch_accum.set_current_section('post')
                    for copy_destination in i_node["copy"]:
                        need_to_copy = True
                        destination_file_resolved_path = var_stack.ResolveStrToStr(copy_destination.value)
                        if os.path.isfile(destination_file_resolved_path) and expected_checksum is not None:
                            checksums_match = utils.check_file_checksum(file_path=destination_file_resolved_path, expected_checksum=expected_checksum)
                            need_to_copy = not checksums_match
                        if need_to_copy:
                            destination_path = var_stack.ResolveStrToStr(copy_destination.value)
                            destination_folder, destination_file_name = os.path.split(destination_path)
                            self.batch_accum += self.platform_helper.mkdir(destination_folder)
                            self.batch_accum += self.platform_helper.copy_tool.copy_file_to_file(file_path,
                                                                                                 destination_path,
                                                                                                 link_dest=True)
                            self.platform_helper.progress("copy cached file to {}".format(destination_path))

    def create_variables_assignment(self, in_batch_accum):
        in_batch_accum.set_current_section("assign")
        for identifier in var_stack:
            if identifier not in self.do_not_write_vars:
                in_batch_accum += self.platform_helper.var_assign(identifier, var_stack.ResolveVarToStr(identifier, list_sep=" ", default=""))  # var_stack[identifier].resolved_num

    def calc_user_cache_dir_var(self, make_dir=True):
        if "USER_CACHE_DIR" not in var_stack:
            os_family_name = var_stack.ResolveVarToStr("__CURRENT_OS__")
            if os_family_name == "Mac":
                user_cache_dir_param = "$(COMPANY_NAME)/$(INSTL_EXEC_DISPLAY_NAME)"
                user_cache_dir = appdirs.user_cache_dir(user_cache_dir_param)
            elif os_family_name == "Win":
                user_cache_dir = appdirs.user_cache_dir("$(INSTL_EXEC_DISPLAY_NAME)", "$(COMPANY_NAME)")
            elif os_family_name == "Linux":
                user_cache_dir_param = "$(COMPANY_NAME)/$(INSTL_EXEC_DISPLAY_NAME)"
                user_cache_dir = appdirs.user_cache_dir(user_cache_dir_param)
            else:
                raise RuntimeError("Unknown operating system "+os_family_name)
            var_description = "from InstlInstanceBase.get_user_cache_dir"
            var_stack.set_var("USER_CACHE_DIR", var_description).append(user_cache_dir)
            var_stack.get_configVar_obj("USER_CACHE_DIR").freeze_values_on_first_resolve = True
        if make_dir:
            user_cache_dir_resolved = var_stack.ResolveVarToStr("USER_CACHE_DIR")
            os.makedirs(user_cache_dir_resolved, exist_ok=True)

    def get_default_sync_dir(self, continue_dir=None, make_dir=True):
        self.calc_user_cache_dir_var()
        if continue_dir:
            retVal = os.path.join("$(USER_CACHE_DIR)", continue_dir)
        else:
            retVal = "$(USER_CACHE_DIR)"
        # print("1------------------", user_cache_dir, "-", from_url, "-", retVal)
        if make_dir and retVal:
            retVal = var_stack.ResolveStrToStr(retVal)
            os.makedirs(retVal, exist_ok=True)
        return retVal

    def relative_sync_folder_for_source(self, source):
        source_path, source_type = source[0], source[1]
        if source_type in ('!dir', '!file'):
            retVal = "/".join(source_path.split("/")[0:-1])
        elif source_type in ('!dir_cont', ):
            retVal = source_path
        else:
            raise ValueError("unknown tag for source " + source_path + ": " + source_type)
        return retVal

    def relative_sync_folder_for_source_table(self, adjusted_source, source_type):
        if source_type in ('!dir', '!file'):
            retVal = "/".join(adjusted_source.split("/")[0:-1])
        elif source_type in ('!dir_cont', ):
            retVal = adjusted_source
        else:
            raise ValueError("unknown tag for source " + adjusted_source + ": " + source_type)
        return retVal

    def write_batch_file(self, in_batch_accum, file_name_post_fix=""):
        assert "__MAIN_OUT_FILE__" in var_stack

        var_stack.set_var("TOTAL_ITEMS_FOR_PROGRESS_REPORT").append(
            str(self.platform_helper.num_items_for_progress_report))

        var_stack.freeze_vars_on_first_resolve()
        self.create_variables_assignment(in_batch_accum)

        in_batch_accum.set_current_section('pre')
        in_batch_accum += self.platform_helper.get_install_instructions_prefix()
        in_batch_accum.set_current_section('post')
        in_batch_accum += self.platform_helper.get_install_instructions_postfix()
        lines = in_batch_accum.finalize_list_of_lines()
        for line in lines:
            if type(line) != str:
                raise TypeError("Not a string", type(line), line)

        # replace unresolved var references to native OS var references, e.g. $(HOME) would be %HOME% on Windows and ${HOME} one Mac
        lines_after_var_replacement = [value_ref_re.sub(self.platform_helper.var_replacement_pattern, line) for line in lines]
        output_text = "\n".join(lines_after_var_replacement)

        out_file = var_stack.ResolveVarToStr("__MAIN_OUT_FILE__")
        out_file += file_name_post_fix
        out_file = os.path.abspath(out_file)
        d_path, f_name = os.path.split(out_file)
        os.makedirs(d_path, exist_ok=True)
        with utils.write_to_file_or_stdout(out_file) as fd:
            fd.write(output_text)
            fd.write('\n')

        if out_file != "stdout":
            self.out_file_realpath = os.path.realpath(out_file)
            # chmod to 0777 so that file created under sudo, can be re-written under regular user.
            # However regular user cannot chmod for file created under sudo, hence the try/except
            try:
                os.chmod(self.out_file_realpath, 0o777)
            except Exception:
                pass
        else:
            self.out_file_realpath = "stdout"
        msg = " ".join(
            (self.out_file_realpath, str(self.platform_helper.num_items_for_progress_report), "progress items"))
        print(msg)

    def run_batch_file(self):
        from subprocess import Popen

        p = Popen([self.out_file_realpath], executable=self.out_file_realpath, shell=False)
        unused_stdout, unused_stderr = p.communicate()
        return_code = p.returncode
        if return_code != 0:
            raise SystemExit(self.out_file_realpath + " returned exit code " + str(return_code))

    def write_program_state(self):

        state_file = var_stack.ResolveVarToStr("__MAIN_STATE_FILE__")
        with utils.write_to_file_or_stdout(state_file) as fd:
            aYaml.writeAsYaml(self, fd)

    def read_index(self, a_node, *args, **kwargs):
        self.items_table.read_index_node(a_node)

    def find_cycles(self):
        try:
            from . import installItemGraph

            depend_graph = installItemGraph.create_dependencies_graph(self.items_table)
            depend_cycles = installItemGraph.find_cycles(depend_graph)
            if not depend_cycles:
                print("No depend cycles found")
            else:
                for cy in depend_cycles:
                    print("depend cycle:", " -> ".join(cy))
            inherit_graph = installItemGraph.create_inheritItem_graph(self.items_table)
            inherit_cycles = installItemGraph.find_cycles(inherit_graph)
            if not inherit_cycles:
                print("No inherit cycles found")
            else:
                for cy in inherit_cycles:
                    print("inherit cycle:", " -> ".join(cy))
        except ImportError:  # no installItemGraph, no worry
                print("Could not load installItemGraph")

    def needs(self, iid, all_iids_set=None, cache=None):
        if cache is None:
            cache = dict()
        if iid in cache:
            return list(sorted(cache[iid]))
        if all_iids_set is None:
            all_iids_set = set(self.items_table.get_all_iids())

        retVal = set()
        depends_from_db = sorted(self.items_table.get_resolved_details_value_for_iid(iid, 'depends',unique_values=True))
        for dep in depends_from_db:
            if dep in all_iids_set:
                retVal.add(dep)
                retVal.update(self.needs(dep, all_iids_set, cache))
            else:
                retVal.append(dep + "(missing)")
        cache[iid] = retVal
        return list(sorted(retVal))

    def needed_by(self, iid, graph=None):
        try:
            from . import installItemGraph
            if not graph:
                graph = installItemGraph.create_dependencies_graph(self.items_table)
            needed_by_list = installItemGraph.find_needed_by(graph, iid)
            return sorted(needed_by_list)
        except ImportError:  # no installItemGraph, no worry
            print("Could not load installItemGraph")
            return None

    def read_info_map_from_file(self, info_map_from_file_path):
        self.info_map_table.read_from_file(info_map_from_file_path, a_format="text")

    def repo_rev_to_folder_hierarchy(self, repo_rev):
        if not self.num_digits_repo_rev_hierarchy:
            self.num_digits_repo_rev_hierarchy=int(var_stack.ResolveVarToStr("NUM_DIGITS_REPO_REV_HIERARCHY"))
        if not self.num_digits_per_folder_repo_rev_hierarchy:
            self.num_digits_per_folder_repo_rev_hierarchy=int(var_stack.ResolveVarToStr("NUM_DIGITS_PER_FOLDER_REPO_REV_HIERARCHY"))
        zero_pad_repo_rev = str(repo_rev).zfill(self.num_digits_repo_rev_hierarchy)
        by_groups = [zero_pad_repo_rev[i:i+self.num_digits_per_folder_repo_rev_hierarchy] for i in range(0, len(zero_pad_repo_rev), self.num_digits_per_folder_repo_rev_hierarchy)]
        retVal = "/".join(by_groups)
        return retVal
