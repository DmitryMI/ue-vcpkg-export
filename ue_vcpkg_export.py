import argparse
import fnmatch
import json
import os
import re
import shutil
import subprocess
import logging
import sys

from templated_string import TemplatedString


class UeVcpkgExport:
    class PackageRule:
        def __init__(
                self,
                package_vcpkg_name,
                package_rules_all: dict
        ):
            trimmed_name = UeVcpkgExport.trim_vcpkg_name(package_vcpkg_name)
            self.do_export = True
            self.do_reference = True
            self.export_name = UeVcpkgExport.generate_camel_case_module_name(trimmed_name)
            self.reference_name = self.export_name

            rules = UeVcpkgExport.PackageRule._find_package_rule(package_vcpkg_name, package_rules_all)
            if rules is not None:
                self.do_export = rules["do_export"] if "do_export" in rules else self.do_export
                self.do_reference = rules["do_reference"] if "do_reference" in rules else self.do_reference
                self.export_name = rules["export_name"] if "export_name" in rules else self.export_name
                self.reference_name = rules["reference_name"] if "reference_name" in rules else self.reference_name

        @staticmethod
        def _find_package_rule(package_name: str, package_rules: dict):
            for package_regex, rules in package_rules.items():
                if re.match(package_regex, package_name):
                    return rules
            return None

    def __init__(
            self,
            package: str,
            vcpkg_dir: str,
            packages_dict: dict,
            package_rules: dict | None,
            triplets: list[str],
            fn_pattern: list[str],
            extension_filter: list[str],
            logger_obj=None
    ):
        self._package = package
        self._packages_dict = packages_dict
        self._vcpkg_dir = vcpkg_dir
        self._logger = logger_obj or logging.getLogger("UeVcpkgExport")
        self._dependencies: list[UeVcpkgExport] = []
        self._vcpkg_exe = os.path.join(self._vcpkg_dir, "vcpkg.exe")

        if self._package not in self._packages_dict:
            self._packages_dict[self._package] = self

        self._package_rules = package_rules

        self._fn_pattern = fn_pattern
        self._extension_filter = extension_filter
        self._triplets = triplets

        self._walk_dependencies()

    def _walk_dependencies(self):
        cmd = [self._vcpkg_exe, "depend-info", self._package]
        cmd_str = " ".join(cmd)
        self._logger.debug(f"Exec: {cmd_str}")
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        depend_info = result.stderr.decode("utf-8")
        depend_info_lines = depend_info.split("\n")

        for depend_line in depend_info_lines:
            if depend_line.startswith("warning"):
                continue

            depend_line = depend_line.strip()

            if depend_line.strip() == "":
                continue

            if self._package in depend_line:
                continue

            dependency_package = depend_line.split(":")[0]

            if dependency_package in self._packages_dict:
                self._dependencies.append(self._packages_dict[dependency_package])
            else:
                exporter = UeVcpkgExport(
                    dependency_package,
                    self._vcpkg_dir,
                    self._packages_dict,
                    self._package_rules,
                    self._triplets,
                    self._fn_pattern,
                    self._extension_filter,
                    self._logger
                )
                self._dependencies.append(exporter)

    @staticmethod
    def trim_vcpkg_name(vcpkg_name: str):
        if "[" not in vcpkg_name:
            return vcpkg_name

        bracket_index = vcpkg_name.index("[")
        return vcpkg_name[:bracket_index]

    @staticmethod
    def _get_dynamic_library_extension(triplet: str):
        triplet_segments = triplet.split("-")
        os_name = triplet_segments[1]
        if os_name == "windows":
            return ".dll"
        elif os_name == "linux":
            return ".so"
        else:
            return None

    @staticmethod
    def _get_files_extension_filter(files: list[str], ext: str):
        return filter(lambda file: file.endswith(ext), files)

    @staticmethod
    def _generate_runtime_dependencies(triplet: str, owned_files: list[str]):
        ext = UeVcpkgExport._get_dynamic_library_extension(triplet)
        dlls = list(UeVcpkgExport._get_files_extension_filter(owned_files, ext))
        items = []
        for file in dlls:
            items.append(f"Path.Combine(ModuleDirectory, \"{triplet}\", \"{file}\")")
        return items

    @staticmethod
    def _generate_public_library_dependencies(triplet: str, owned_files: list[str]):
        libs = UeVcpkgExport._get_files_extension_filter(owned_files, ".lib")
        items = []
        for file in libs:
            items.append(f"Path.Combine(ModuleDirectory, \"{triplet}\", \"{file}\")")
        return items

    @staticmethod
    def _generate_public_include_paths(triplet: str):
        return [f"Path.Combine(ModuleDirectory, \"{triplet}\", \"include\")"]

    def _generate_public_definitions(self):
        text = f"\"WITH_{self.get_module_export_name().upper()}\""
        return [text]

    @staticmethod
    def _get_camel_case(s: str):
        output = ''.join(x for x in s.title() if x.isalnum())
        return output[0].upper() + output[1:]

    @staticmethod
    def generate_camel_case_module_name(package_name: str):
        return UeVcpkgExport._get_camel_case(package_name)

    def get_module_export_name(self):
        rule = UeVcpkgExport.PackageRule(self._package, self._package_rules)
        return rule.export_name

    def get_module_reference_name(self):
        rule = UeVcpkgExport.PackageRule(self._package, self._package_rules)
        return rule.reference_name

    def should_reference_module(self):
        rule = UeVcpkgExport.PackageRule(self._package, self._package_rules)
        return rule.do_reference

    def should_export_module(self):
        rule = UeVcpkgExport.PackageRule(self._package, self._package_rules)
        return rule.do_export

    @staticmethod
    def _generate_csharp_dictionary_record(key: str, value: list[str] | str):
        if isinstance(value, list):
            return "{" + f"\"{key}\"," + "new string[] {" + ", ".join(value) + "}}"
        else:
            return "{" + f"\"{key}\", \"{value}\"" + "}"

    @staticmethod
    def _generate_csharp_dictionary(records: dict):
        result = "new Dictionary<string, object>{"

        record_strs = []
        for key, value in records.items():
            record_strs.append(UeVcpkgExport._generate_csharp_dictionary_record(key, value))

        result += ", ".join(record_strs)

        result += "}"
        return result

    @staticmethod
    def _generate_csharp_array(values: list):
        result = "{" + ", ".join(values) + "}"
        return result

    @staticmethod
    def _triplet_to_unreal_platform(triplet: str):
        triplet_segments = triplet.split("-")
        platform = triplet_segments[1]
        if platform == "windows":
            return "Win64"
        else:
            return None

    @staticmethod
    def _triplet_to_unreal_arch(triplet: str):
        triplet_segments = triplet.split("-")
        platform = triplet_segments[0]
        if platform == "x64":
            return "X64"
        else:
            return None

    def _generate_triplet_file_record(self, triplet: str, triplet_files: list[str]):
        defs = self._generate_public_definitions()
        includes = UeVcpkgExport._generate_public_include_paths(triplet)
        libs = UeVcpkgExport._generate_public_library_dependencies(triplet, triplet_files)
        dlls = UeVcpkgExport._generate_runtime_dependencies(triplet, triplet_files)

        csharp_dictionary = UeVcpkgExport._generate_csharp_dictionary(
            {
                "PublicDefinitions": defs,
                "PublicIncludePaths": includes,
                "PublicAdditionalLibraries": libs,
                "RuntimeDependencies": dlls,
                "MatchingArchitecture": UeVcpkgExport._triplet_to_unreal_arch(triplet),
                "MatchingTargetPlatform": UeVcpkgExport._triplet_to_unreal_platform(triplet)
            }
        )
        return csharp_dictionary

    def _generate_build_cs(self, module_build_template_path: str, triplet_files: dict[str, list[str]]):
        with open(module_build_template_path, "r") as file_in:
            module_build_template = TemplatedString(file_in.read())

        triplet_file_records = []
        for triplet, files in triplet_files.items():
            triplet_file_record = self._generate_triplet_file_record(triplet, files)
            triplet_file_records.append(triplet_file_record)

        triplet_file_records_csharp = UeVcpkgExport._generate_csharp_array(triplet_file_records)
        dependency_module_names = self._generate_dependency_module_names()
        dependency_module_loader_names = self._generate_dependency_module_loader_names()
        build_cs = module_build_template.substitute(
            {
                "ModuleName": self.get_module_export_name(),
                "TripletFileRecords": triplet_file_records_csharp,
                "DependencyModuleNames": dependency_module_names,
                "DependencyModuleLoaderNames": dependency_module_loader_names
            }
        )

        return build_cs

    def _generate_loader_build_cs(self, module_loader_build_cs_path):
        with open(module_loader_build_cs_path, "r") as file_in:
            module_loader_build_template = TemplatedString(file_in.read())

        build_cs = module_loader_build_template.substitute(
            {
                "ModuleName": self.get_module_export_name(),
            }
        )

        return build_cs

    @staticmethod
    def _triplet_to_unreal_ini_platform(triplet: str):
        triplet_segments = triplet.split("-")
        platform = triplet_segments[1]
        return platform[0].upper() + platform[1:]

    @staticmethod
    def _is_loader_module_needed(triplet_files: dict[str, list[str]]):
        for triplet, files in triplet_files.items():
            ext = UeVcpkgExport._get_dynamic_library_extension(triplet)
            dlls = list(UeVcpkgExport._get_files_extension_filter(files, ext))
            if dlls:
                return True

        return False

    def get_triplet_files(self):
        triplet_files = {}
        for triplet in self._triplets:
            owned_files = self.get_owned_files(triplet, self._fn_pattern, self._extension_filter)
            if not owned_files:
                self._logger.error(f"Package {self._package} has no owned files for triplet {triplet}!")
                quit(1)

            triplet_files[triplet] = owned_files
        return triplet_files

    @staticmethod
    def _generate_dynamic_library_binary_names(triplet_files: dict[str, list[str]]):
        map_items = []
        for triplet, files in triplet_files.items():
            ext = UeVcpkgExport._get_dynamic_library_extension(triplet)
            dlls = list(UeVcpkgExport._get_files_extension_filter(files, ext))
            items = [f"TEXT(\"{os.path.basename(dll)}\")" for dll in dlls]
            ini_platform = UeVcpkgExport._triplet_to_unreal_ini_platform(triplet)
            map_items.append(f"TEXT(\"{ini_platform}\")" + ", {" + ", ".join(items) + "}")

        return "{{" + ", ".join(map_items) + "}}"

    def get_package(self):
        return self._package

    def __repr__(self):
        return f"UeVcpkgExport[package: {self._package}]"

    def __str__(self):
        return f"UeVcpkgExport[package: {self._package}]"

    def _generate_dependency_module_names(self):
        names = set()
        for dependency_obj in self._dependencies:
            if not dependency_obj.should_reference_module():
                continue

            module_name = dependency_obj.get_module_reference_name()
            names.add(f"\"{module_name}\"")
        return ", ".join(names)

    def _generate_dependency_module_loader_names(self):
        names = set()
        for dependency_obj in self._dependencies:
            if not dependency_obj.should_reference_module():
                continue

            if not dependency_obj.should_export_module():
                continue

            dependency_triplet_files = dependency_obj.get_triplet_files()

            if not UeVcpkgExport._is_loader_module_needed(dependency_triplet_files):
                continue

            module_name = dependency_obj.get_module_reference_name()
            names.add(f"\"{module_name}Loader\"")
        return ", ".join(names)

    def _generate_module_loader_h(self, module_loader_h_template_path: str, triplet_files: dict[str, list[str]]):
        with open(module_loader_h_template_path, "r") as file_in:
            module_loader_h_template = TemplatedString(file_in.read())
        module_name = self.get_module_export_name()

        text = module_loader_h_template.substitute(
            {
                "ModuleName": module_name,
                "DynamicLibraryBinaryNames": UeVcpkgExport._generate_dynamic_library_binary_names(triplet_files),
                "DependencyModuleLoaderNames": self._generate_dependency_module_loader_names()
            }
        )
        return text

    def _generate_module_loader_cpp(self, module_loader_cpp_template_path: str):
        with open(module_loader_cpp_template_path, "r") as file_in:
            module_loader_cpp_template = TemplatedString(file_in.read())

        module_name = self.get_module_export_name()
        text = module_loader_cpp_template.substitute(
            {
                "ModuleName": module_name,
            }
        )
        return text

    def build(self, triplet: str | None, overlay_triplet: str | None):
        self._logger.info(f"Building {self._package} and all dependencies...")

        package_fullname = f"{self._package}:{triplet}" if triplet is not None else self._package
        cmd = [self._vcpkg_exe, "install", package_fullname]
        if overlay_triplet is not None:
            cmd.append("--overlay-triplets")
            cmd.append(os.path.join(self._vcpkg_dir, overlay_triplet))

        cmd_str = " ".join(cmd)
        self._logger.info(f"Exec: {cmd_str}")
        subprocess.run(cmd)

        self._logger.info("Building finished")

    def get_owned_files(
            self,
            triplet: str,
            fnmatch_patterns: list[str] | None,
            extensions_whitelist: list[str] | None,
            convert_to_absolute: bool = False
    ):
        owned_files = []

        info_dir = os.path.join(self._vcpkg_dir, "installed", "vcpkg", "info")
        info_files = os.listdir(info_dir)
        owned_lines = []
        for info_file in info_files:
            info_file_name = os.path.splitext(info_file)[0]
            info_segments = info_file_name.split("_")
            info_package = info_segments[0]
            info_version = info_segments[1]
            info_triplet = info_segments[2]
            if info_package != UeVcpkgExport.trim_vcpkg_name(self._package):
                continue
            if info_triplet != triplet:
                continue

            with open(os.path.join(info_dir, info_file), "r") as info_file_io:
                owned_lines = info_file_io.readlines()
            break

        for owns_path_relative in owned_lines:
            owns_path_relative = owns_path_relative.strip()
            if owns_path_relative == "":
                continue

            if fnmatch_patterns:
                has_path_match = False
                for pattern in fnmatch_patterns:
                    if fnmatch.fnmatch(owns_path_relative, os.path.join(triplet, pattern)):
                        has_path_match = True
                        break
            else:
                has_path_match = True

            if extensions_whitelist:
                has_extension_match = False
                for extension in extensions_whitelist:
                    if owns_path_relative.endswith(extension):
                        has_extension_match = True
                        break
            else:
                has_extension_match = True

            if has_path_match and has_extension_match:
                if convert_to_absolute:
                    owns_full_path = os.path.join(self._vcpkg_dir, "installed", owns_path_relative)
                    owned_files.append(owns_full_path)
                else:
                    owned_files.append(owns_path_relative.replace(triplet + "/", ""))

        return owned_files

    def export(
            self,
            module_build_cs_dir: str,
            module_loader_build_cs_dir: str,
            module_loader_h_dir: str,
            module_loader_cpp_dir: str,
            output_dir: str,
            overwrite: bool = False
    ):
        if not self.should_export_module():
            self._logger.warning(f"Exporting disabled for {self._package} in package rules.")
            return

        module_name = self.get_module_export_name()
        module_loader_name = module_name + "Loader"

        module_output_dir = os.path.join(output_dir, module_name)
        module_loader_output_dir = os.path.join(output_dir, module_loader_name)
        if os.path.exists(module_output_dir):
            if not overwrite:
                self._logger.warning(
                    f"Directory {module_output_dir} already exists. Module {module_name} export skipped. Use --overwrite to enable overwriting.")
                return
            else:
                shutil.rmtree(module_output_dir)

        if os.path.exists(module_loader_output_dir):
            if not overwrite:
                self._logger.warning(
                    f"Directory {module_loader_output_dir} already exists. Module {module_name} export skipped. Use --overwrite to enable overwriting.")
                return
            else:
                shutil.rmtree(module_loader_output_dir)

        module_private_dir = os.path.join(module_output_dir, "Private")
        module_public_dir = os.path.join(module_output_dir, "Public")

        os.makedirs(module_private_dir)
        os.makedirs(module_public_dir)

        triplet_files = self.get_triplet_files()

        build_cs_text = self._generate_build_cs(module_build_cs_dir, triplet_files)
        build_cs_path = os.path.join(module_output_dir, f"{module_name}.Build.cs")
        with open(build_cs_path, "w") as file_out:
            file_out.write(build_cs_text)

        if UeVcpkgExport._is_loader_module_needed(triplet_files):
            module_loader_private_dir = os.path.join(module_loader_output_dir, "Private")
            module_loader_public_dir = os.path.join(module_loader_output_dir, "Public")
            os.makedirs(module_loader_private_dir)
            os.makedirs(module_loader_public_dir)

            loader_build_cs_text = self._generate_loader_build_cs(module_loader_build_cs_dir)
            module_loader_h_text = self._generate_module_loader_h(module_loader_h_dir, triplet_files)
            module_loader_cpp_text = self._generate_module_loader_cpp(module_loader_cpp_dir)
            loader_build_cs_path = os.path.join(module_loader_output_dir, f"{module_name}Loader.Build.cs")
            module_loader_h_path = os.path.join(module_loader_private_dir, f"{module_name}LoaderModule.h")
            module_loader_cpp_path = os.path.join(module_loader_private_dir, f"{module_name}LoaderModule.cpp")
            with open(module_loader_h_path, "w") as file_out:
                file_out.write(module_loader_h_text)
            with open(module_loader_cpp_path, "w") as file_out:
                file_out.write(module_loader_cpp_text)

            with open(loader_build_cs_path, "w") as file_out:
                file_out.write(loader_build_cs_text)

        for triplet in self._triplets:
            files = triplet_files[triplet]
            triplet_output_dir = os.path.join(module_output_dir, triplet)
            for file in files:
                file_path_src = os.path.join(self._vcpkg_dir, "installed", triplet, file)
                file_path_dest = os.path.join(triplet_output_dir, file)
                os.makedirs(os.path.dirname(file_path_dest), exist_ok=True)
                shutil.copy(file_path_src, file_path_dest)

    def print_dependency_tree(self, printer, level=0):
        offset = "\t" * level
        printer(f"{offset}{self._package}")
        for dependency in self._dependencies:
            dependency.print_dependency_tree(printer, level + 1)


def main():
    logging.basicConfig()
    print(sys.argv)
    parser = argparse.ArgumentParser()

    parser.add_argument("--target_package", type=str, required=True)
    parser.add_argument("--verbosity", default="INFO")
    parser.add_argument("--vcpkg_dir")
    parser.add_argument("--output_dir", default="export")
    parser.add_argument("--triplets", nargs="+", default=["x64-windows"])
    parser.add_argument("--overlay-triplets", default="triplets_custom")
    parser.add_argument("--export_fnmatch", nargs="+", default=["include/**", "lib/**", "bin/**"])
    parser.add_argument("--export_extension", nargs="+", default=[".dll", ".so", ".lib", ".h", ".hpp"])

    parser.add_argument("--loader_build_cs_template", default="templates/ModuleLoader.Build.cs.in")
    parser.add_argument("--module_build_cs_template", default="templates/Module.Build.cs.in")
    parser.add_argument("--module_loader_h_template", default="templates/ModuleLoader.h.in")
    parser.add_argument("--module_loader_cpp_template", default="templates/ModuleLoader.cpp.in")

    parser.add_argument("--with_dependencies", action="store_true")
    parser.add_argument("--overwrite", action="store_true")

    parser.add_argument("--package_rules", default="package-rules.json")

    args = parser.parse_args()

    logger = logging.getLogger("UeVcpkgExport")
    logger.setLevel(args.verbosity)

    package_rules_path = args.package_rules
    package_rules = []
    if package_rules_path is not None:
        with open(package_rules_path, "r") as package_rules_file:
            package_rules = json.load(package_rules_file)["package_rules"]

    packages_dict = {}
    export_obj = UeVcpkgExport(
        args.target_package,
        args.vcpkg_dir,
        packages_dict,
        package_rules,
        args.triplets,
        args.export_fnmatch,
        args.export_extension,
        logger)
    # export_obj.print_dependency_tree(lambda msg: logger.info(msg))
    for triplet in args.triplets:
        export_obj.build(triplet, args.overlay_triplets)

    if not args.with_dependencies:
        export_obj.export(
            args.build_cs_template,
            args.module_h_template,
            args.module_cpp_template,
            args.output_dir,
            args.overwrite
        )
    else:
        for package_name, package_export in packages_dict.items():
            package_export.export(
                args.module_build_cs_template,
                args.loader_build_cs_template,
                args.module_loader_h_template,
                args.module_loader_cpp_template,
                args.output_dir,
                args.overwrite
            )


if __name__ == '__main__':
    main()
