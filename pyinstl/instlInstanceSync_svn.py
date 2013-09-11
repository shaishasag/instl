#!/usr/bin/env python2.7
from __future__ import print_function

import abc

import logging

import pyinstl.log_utils
from pyinstl.log_utils import func_log_wrapper
from pyinstl.utils import *

class InstlInstanceSync(object):
    """  Base class for sync object .
    """
    __metaclass__ = abc.ABCMeta
    @abc.abstractmethod
    def init_sync_vars(self):
        """ sync specific initialisation of variables """
        pass
    @abc.abstractmethod
    def create_sync_instructions(self, installState):
        """ sync specific creation of sync instructions """
        pass

class InstlInstanceSync_svn(InstlInstanceSync):
    """  Class to create sync instruction using svn.
    """
    @func_log_wrapper
    def __init__(self, instlInstance):
        self.instlInstance = instlInstance

    @func_log_wrapper
    def init_sync_vars(self):
        var_description = "from InstlInstanceBase.init_sync_vars"
        if "SVN_REPO_URL" not in self.instlInstance.cvl:
            raise ValueError("'SVN_REPO_URL' was not defined")
        if "SVN_CLIENT_PATH" not in self.instlInstance.cvl:
            raise ValueError("'SVN_CLIENT_PATH' was not defined")
        svn_client_full_path = self.instlInstance.search_paths_helper.find_file_with_search_paths(self.instlInstance.cvl.get_str("SVN_CLIENT_PATH"))
        self.instlInstance.cvl.set_variable("SVN_CLIENT_PATH", var_description).append(svn_client_full_path)

        if "REPO_REV" not in self.instlInstance.cvl:
            self.instlInstance.cvl.set_variable("REPO_REV", var_description).append("HEAD")
        if "BASE_SRC_URL" not in self.instlInstance.cvl:
            self.instlInstance.cvl.set_variable("BASE_SRC_URL", var_description).append("$(SVN_REPO_URL)/$(TARGET_OS)")

        if "LOCAL_SYNC_DIR" not in self.instlInstance.cvl:
            self.instlInstance.cvl.set_variable("LOCAL_SYNC_DIR", var_description).append(self.instlInstance.get_default_sync_dir())

        if "BOOKKEEPING_DIR_URL" not in self.instlInstance.cvl:
            self.instlInstance.cvl.set_variable("BOOKKEEPING_DIR_URL").append("$(SVN_REPO_URL)/instl")
        bookkeeping_relative_path = relative_url(self.instlInstance.cvl.get_str("SVN_REPO_URL"), self.instlInstance.cvl.get_str("BOOKKEEPING_DIR_URL"))
        self.instlInstance.cvl.set_variable("REL_BOOKKIPING_PATH", var_description).append(bookkeeping_relative_path)

        rel_sources = relative_url(self.instlInstance.cvl.get_str("SVN_REPO_URL"), self.instlInstance.cvl.get_str("BASE_SRC_URL"))
        self.instlInstance.cvl.set_variable("REL_SRC_PATH", var_description).append(rel_sources)

        for identifier in ("SVN_REPO_URL", "SVN_CLIENT_PATH", "REL_SRC_PATH", "REPO_REV", "BASE_SRC_URL", "BOOKKEEPING_DIR_URL"):
            logging.debug("... %s: %s", identifier, self.instlInstance.cvl.get_str(identifier))

    @func_log_wrapper
    def create_sync_instructions(self, installState):
        num_items_for_progress_report = len(installState.full_install_items) + 2 # one for a dummy last item, one for index sync
        current_item_for_progress_report = 0
        installState.append_instructions('sync', self.instlInstance.create_echo_command("Progress: synced {current_item_for_progress_report} of {num_items_for_progress_report}; from $(BASE_SRC_URL)".format(**locals())))
        current_item_for_progress_report += 1
        installState.indent_level += 1
        installState.extend_instructions('sync', self.instlInstance.make_directory_cmd("$(LOCAL_SYNC_DIR)"))
        installState.extend_instructions('sync', self.instlInstance.change_directory_cmd("$(LOCAL_SYNC_DIR)"))
        installState.indent_level += 1
        installState.append_instructions('sync', " ".join(('"$(SVN_CLIENT_PATH)"', "co", '"$(BOOKKEEPING_DIR_URL)"', '"$(REL_BOOKKIPING_PATH)"', "--revision", "$(REPO_REV)", "--depth", "infinity")))
        installState.append_instructions('sync', self.instlInstance.create_echo_command("Progress: synced {current_item_for_progress_report} of {num_items_for_progress_report}; index file $(BOOKKEEPING_DIR_URL)".format(**locals())))
        current_item_for_progress_report += 1
        for iid  in installState.full_install_items:
            installi = self.instlInstance.install_definitions_index[iid]
            if installi.source_list():
                for source in installi.source_list():
                    installState.extend_instructions('sync', self.create_svn_sync_instructions_for_source(source))
            installState.append_instructions('sync', self.instlInstance.create_echo_command("Progress: synced {current_item_for_progress_report} of {num_items_for_progress_report}; {installi.iid}: {installi.name}".format(**locals())))
            current_item_for_progress_report += 1
        for iid in installState.orphan_install_items:
            installState.append_instructions('sync', self.instlInstance.create_echo_command("Don't know how to sync "+iid))
        installState.indent_level -= 1
        installState.append_instructions('sync', self.instlInstance.create_echo_command("Progress: synced {current_item_for_progress_report} of {num_items_for_progress_report};  from $(BASE_SRC_URL)".format(**locals())))
        

    @func_log_wrapper
    def create_svn_sync_instructions_for_source(self, source):
        """ source is a tuple (source_folder, tag), where tag is either !file or !dir """
        retVal = list()
        source_url =   '/'.join( ("$(BASE_SRC_URL)", source[0]) )
        target_path =  '/'.join( ("$(REL_SRC_PATH)", source[0]) )
        if source[1] == '!file':
            source_url = '/'.join( source_url.split("/")[0:-1]) # skip the file name sync the whole folder
            target_path = '/'.join( target_path.split("/")[0:-1]) # skip the file name sync the whole folder
        command_parts = ['"$(SVN_CLIENT_PATH)"', "co", '"'+source_url+'"', '"'+target_path+'"', "--revision", "$(REPO_REV)"]
        if source[1] in ('!file', '!files'):
            command_parts.extend( ( "--depth", "files") )
        else:
            command_parts.extend( ( "--depth", "infinity") )
        retVal.append(" ".join(command_parts))

        logging.info("... %s; (%s)", source[0], source[1])
        return retVal
