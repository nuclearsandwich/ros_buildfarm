#!/usr/bin/env python3

# Copyright 2015-2016 Open Source Robotics Foundation, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import os
import subprocess
import sys

from ros_buildfarm.argument import add_argument_output_dir
from ros_buildfarm.catkin_workspace import call_catkin_make_isolated
from ros_buildfarm.catkin_workspace import clean_workspace
from ros_buildfarm.catkin_workspace import ensure_workspace_exists
from ros_buildfarm.common import Scope
from ros_buildfarm.rosdoc_index import RosdocIndex
from ros_buildfarm.rosdoc_lite import get_generator_output_folders

import yaml


def main(argv=sys.argv[1:]):
    parser = argparse.ArgumentParser(
        description="Invoke 'rosdoc_lite' on each package of a workspace")
    parser.add_argument(
        '--rosdistro-name',
        required=True,
        help='The name of the ROS distro to identify the setup file to be '
             'sourced (if available)')
    parser.add_argument(
        '--os-code-name',
        required=True,
        help="The OS code name (e.g. 'xenial')")
    parser.add_argument(
        '--arch',
        required=True,
        help="The architecture (e.g. 'amd64')")
    parser.add_argument(
        '--workspace-root',
        required=True,
        help='The root path of the workspace to compile')
    parser.add_argument(
        '--rosdoc-lite-dir',
        required=True,
        help='The root path of the rosdoc_lite repository')
    parser.add_argument(
        '--catkin-sphinx-dir',
        required=True,
        help='The root path of the catkin-sphinx repository')
    parser.add_argument(
        '--rosdoc-index-dir',
        required=True,
        help='The root path of the rosdoc_index folder')
    parser.add_argument(
        '--canonical-base-url',
        help='The canonical base URL to add to all generated HTML files')
    parser.add_argument(
        'pkg_tuples',
        nargs='*',
        help='A list of package tuples in topological order, each containing '
             'the name, the relative path and optionally the package-relative '
             'path of the rosdoc config file separated by a colon')
    add_argument_output_dir(parser, required=True)
    args = parser.parse_args(argv)

    ensure_workspace_exists(args.workspace_root)
    clean_workspace(args.workspace_root)

    with Scope('SUBSECTION', 'build workspace in isolation and install'):
        env = dict(os.environ)
        env['MAKEFLAGS'] = '-j1'
        rc = call_catkin_make_isolated(
            args.rosdistro_name, args.workspace_root,
            ['--cmake-args', '-DCATKIN_SKIP_TESTING=1',
             '--executor', 'sequential',
             '--event-handlers', 'console_direct+'],
            env=env)
    # TODO compile error should still allow to generate doc from static parts
    if rc:
        return rc

    rosdoc_index = RosdocIndex([
        os.path.join(args.output_dir, args.rosdistro_name),
        os.path.join(args.rosdoc_index_dir, args.rosdistro_name)])

    source_space = os.path.join(args.workspace_root, 'src')
    for pkg_tuple in args.pkg_tuples:
        pkg_name, pkg_subfolder, pkg_rosdoc_config = pkg_tuple.split(':', 2)
        with Scope('SUBSECTION', 'rosdoc_lite - %s' % pkg_name):
            pkg_path = os.path.join(source_space, pkg_subfolder)

            pkg_doc_path = os.path.join(
                args.output_dir, 'api_rosdoc', pkg_name)
            pkg_tag_path = os.path.join(
                args.output_dir, 'symbols', '%s.tag' % pkg_name)

            source_cmd = [
                '.', os.path.join(
                    args.workspace_root, 'install', 'setup.sh'),
            ]
            # for workspaces with only plain cmake packages the setup files
            # generated by cmi won't implicitly source the underlays
            setup_file = '/opt/ros/%s/setup.sh' % args.rosdistro_name
            if os.path.exists(setup_file):
                source_cmd = ['.', setup_file, '&&'] + source_cmd
            rosdoc_lite_cmd = [
                os.path.join(args.rosdoc_lite_dir, 'scripts', 'rosdoc_lite'),
                pkg_path,
                '-o', pkg_doc_path,
                '-g', pkg_tag_path,
                '-t', os.path.join(
                    args.output_dir, 'rosdoc_tags', '%s.yaml' % pkg_name),
            ]
            print("Invoking `rosdoc_lite` for package '%s': %s" %
                  (pkg_name, ' '.join(rosdoc_lite_cmd)))
            pkg_rc = subprocess.call(
                [
                    'sh', '-c',
                    ' '.join(source_cmd) +
                    ' && ' +
                    'PYTHONPATH=%s/src:%s/src:$PYTHONPATH ' % (
                        args.rosdoc_lite_dir, args.catkin_sphinx_dir) +
                    ' '.join(rosdoc_lite_cmd)
                ], stderr=subprocess.STDOUT, cwd=pkg_path)
            if pkg_rc:
                rc = pkg_rc

            # only if rosdoc runs generates a symbol file
            # create the corresponding location file
            if os.path.exists(pkg_tag_path):
                data = {
                    'docs_url': '../../../api/%s/html' % pkg_name,
                    'location': '%s/symbols/%s.tag' %
                    (args.rosdistro_name, pkg_name),
                    'package': pkg_name,
                }

                # fetch generator specific output folders from rosdoc_lite
                if pkg_rosdoc_config:
                    output_folders = get_generator_output_folders(
                        pkg_rosdoc_config, pkg_name)
                    for generator, output_folder in output_folders.items():
                        data['%s_output_folder' % generator] = output_folder

                rosdoc_index.locations[pkg_name] = [data]

            if args.canonical_base_url:
                add_canonical_link(
                    pkg_doc_path, '%s/%s/api/%s' %
                    (args.canonical_base_url, args.rosdistro_name, pkg_name))

            # merge manifest.yaml files
            rosdoc_manifest_yaml_file = os.path.join(
                pkg_doc_path, 'manifest.yaml')
            job_manifest_yaml_file = os.path.join(
                args.output_dir, 'manifests', pkg_name, 'manifest.yaml')
            if os.path.exists(rosdoc_manifest_yaml_file):
                with open(rosdoc_manifest_yaml_file, 'r') as h:
                    rosdoc_data = yaml.load(h)
            else:
                # if rosdoc_lite failed to generate the file
                rosdoc_data = {}
            with open(job_manifest_yaml_file, 'r') as h:
                job_data = yaml.load(h)
            rosdoc_data.update(job_data)
            with open(rosdoc_manifest_yaml_file, 'w') as h:
                yaml.safe_dump(rosdoc_data, h, default_flow_style=False)

    rosdoc_index.write_modified_data(
        args.output_dir, ['locations'])

    return rc


def add_canonical_link(base_path, base_link):
    print("add canonical link '%s' to all html files under '%s'" %
          (base_link, base_path))
    for path, dirs, files in os.walk(base_path):
        for filename in [f for f in files if f.endswith('.html')]:
            filepath = os.path.join(path, filename)
            try:
                with open(filepath, 'rb') as h:
                    data = h.read()
            except Exception:
                print("error reading file '%s'" % filepath)
                raise
            if data.find(b'rel="canonical"') != -1:
                continue
            rel_path = os.path.relpath(filepath, base_path)
            link = os.path.join(base_link, rel_path)
            data = data.replace(
                b'</head>', b'<link rel="canonical" href="' + link.encode() +
                b'" />\n</head>', 1)
            with open(filepath, 'wb') as h:
                h.write(data)


if __name__ == '__main__':
    sys.exit(main())
