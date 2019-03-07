
# Copyright 2018 ReactiveOps

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at

#     http://www.apache.org/licenses/LICENSE-2.0

# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import logging
import traceback
import glob
import re
import oyaml as yaml
import subprocess

from pentagon.component import ComponentBase

from pentagon.helpers import render_template, merge_dict


class Rodd(ComponentBase):
    _files_directory = os.path.dirname(__file__) + "/files/"
    _defaults = {'options': {}}
    _path = os.path.dirname(__file__)
    _global_definitions = {}
    _exceptions = []
    _resources = {}

    def _render_directory_templates(self, data):
        """ Overide Component method _render_directory_templates.
        Loop and use render_template helper method on all templates
        in destination directory, but use the _data['name'] as the base
        for the target file instead of the tempate name itself """

        # Here is where it differs from the Component._render_directory_templates()
        target_file_name = os.path.normpath("{}{}.tf".format(self._destination, data['resource_name']))

        render_template(self.template_file_name, self._files_directory, target_file_name, data, delete_template=False, overwrite=self._overwrite)

    def _flatten_options(self):
        """ If there is a options key in the _data, flatten it
        This makes transformation from datadog json easier """
        if self._data.get('options'):
            for key, value in self._data.get('options').iteritems():
                self._data[key] = value

    def add(self):
        """ Copies files and templates from <component>/files and templates the *.jinja files """

        source = self._data.get('source')
        if source is None:
            self._create_tf_file(self._data)
        else:

            item_local_paths = []
            if "." in source:
                item_local_paths = ["{}/{}/{}.yml".format(self._path, self._item_type, ("/").join(source.split(".")))]
            else:
                item_local_paths = glob.glob("{}/{}/{}/*.yml".format(self._path, self._item_type, source))
            logging.debug("{}: {}".format(self._item_type.title(), item_local_paths))

            for local_source_path in item_local_paths:
                data = {}
                resource_id = '.'.join((local_source_path.split('.')[0]).split('/')[-3:])
                logging.debug("Loading {}".format(local_source_path))
                logging.debug("Source is: {} ".format(source))
                logging.debug("Resource id is: {} ".format(resource_id))

                if os.path.isfile(local_source_path) and ('/').join(local_source_path.split('/')[-2:]) in self.exceptions:
                    continue

                with open(local_source_path, 'r') as item_file:
                    item_dict = yaml.load(item_file)
                # If the items are being pulled from a family,
                # then use all the values in the default item
                if len(item_local_paths) > 1:
                    data = item_dict
                else:
                    # Otherwise, overwrite the item values with
                    # the values being passed in
                    data = merge_dict(self._data, item_dict)

                data['definition_defaults'] = merge_dict(data.get('definition_defaults', {}), self._global_definitions)

                logging.debug('Final context: {}'.format(data))
                self._resources[resource_id] = data

    def generate_resource_tf(self, destination, overwrite=False):
        self._overwrite = overwrite
        self._destination = destination

        for key, data in self._resources.items():
            definitions = merge_dict(data.get('definition_defaults', {}), self.definitions, {})
            definition_namespace = definitions.get('namespace', "")
            data['definitions'] = data.get('definitions', {})
            # If resource is namespace specific, create a seaprate resource per namespace
            if data.get('namespaced', False) and isinstance(definition_namespace, list):
                for namespace in definition_namespace:
                    data_copy = data.copy()
                    data_copy['definitions']['namespace'] = namespace
                    self._create_tf_file(data_copy)
            else:
                self._create_tf_file(data)

    def _create_tf_file(self, data):
        try:
            # transform item name
            data = self._replace_definitions(data)

            raw_resource_name = data.get('name', data.get('title', 'Unknown Title'))

            data['resource_name'] = re.sub('^_', '', re.sub('[^0-9a-zA-Z]+', '_', raw_resource_name.lower())).strip('_')
            logging.debug("New Name: {}".format(data['resource_name']))

            self._flatten_options()
            for key in data:
                if type(data[key]) in [unicode, str]:
                    data[key] = data[key].replace('"', '\\"')
            self._remove_init_file()
            self._render_directory_templates(data)

        except Exception as e:
            logging.error("Error occured configuring component")
            logging.error(e)
            logging.debug(traceback.format_exc(e))

    def _validate_tf(self, destination):
        """Validate terraform in the path provided."""
        try:
            if len([file for file in os.listdir(destination) if os.path.isfile(file) and file.endswith(".tf")]) > 0:
                tf = subprocess.check_output(['terraform', 'fmt', destination])
                logging.debug("terraform fmt output:\n{}".format(tf))

                validate = subprocess.check_output(['terraform', 'validate', '--check-variables=false', destination])
        except subprocess.CalledProcessError as validateErr:
            logging.warning("Error validating terraform: {}".format(validateErr.output))

    @property
    def definitions(self):
        """ Return dictionary of merged definitions """
        return merge_dict(self.global_definitions, self._data.get('definitions', {}), clobber=True)

    def _replace_definitions(self, data):
        """ Replace ${definitions} with their value """

        def _replace_definition(string, definitions):
            if type(string) == str:
                for var, value in definitions.iteritems():
                    logging.debug("Replacing Definition: {}:{}".format(var, value))
                    string = string.replace("${%s}" % str(var), str(value))

            return string

        # Locally scopped copy of definitions to add monitor defaults to
        _definitions = merge_dict(data.get('definitions'), data.get('definition_defaults', {}))

        logging.debug("Definitions: {}".format(_definitions))
        for key in data.keys():

            # Just handle the tags and thresholds separately since they are a list.  Probably a
            # better way to do this in the future
            if key == 'tags':
                logging.debug("Found tags: {}".format(data[key]))
                for index, item in enumerate(data[key]):
                    data[key][index] = _replace_definition(data[key][index], _definitions)
            elif key == 'thresholds':
                logging.debug("Found thresholds: {}".format(data[key]))
                for threshold_type in data[key]:
                    data[key][threshold_type] = _replace_definition(data[key][threshold_type], _definitions)
            else:
                data[key] = _replace_definition(data[key], _definitions)
        return data

    @property
    def global_definitions(self):
        return self._global_definitions

    @property
    def exceptions(self):
        exception_paths = []
        for e in self._exceptions:
            exception_paths.append("{}.yml".format(e.replace('.', '/')))
        return exception_paths
