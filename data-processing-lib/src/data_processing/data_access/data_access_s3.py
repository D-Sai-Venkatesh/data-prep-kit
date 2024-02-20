import gzip
import json

from data_processing.data_access import *

import pyarrow as pa
from data_processing.data_access.arrow_s3 import ArrowS3


class DataAccessS3(DataAccess):
    """
    Implementation of the Base Data access class for folder-based data access.
    """

    def __init__(
        self,
        s3_credentials: dict[str, str],
        s3_config: dict[str, str],
        d_sets: list[str],
        checkpoint: bool,
        m_files: int,
    ):
        """
        Create data access class for folder based configuration
        :param s3_credentials: dictionary of cos credentials
        :param s3_config: dictionary of path info
        :param d_sets list of the data sets to use
        :param checkpoint: flag to return only files that do not exist in the output directory
        :param m_files: max amount of files to return
        """
        self.arrS3 = ArrowS3(
            access_key=s3_credentials["access_key"],
            secret_key=s3_credentials["secret_key"],
            endpoint=s3_credentials["cos_url"],
        )
        self.input_folder = s3_config["input_folder"]
        self.output_folder = s3_config["output_folder"]
        self.d_sets = d_sets
        self.checkpoint = checkpoint
        self.m_files = m_files

    def _get_files_folder(
        self, path: str, cm_files: int, max_file_size: int = 0, min_file_size: int = MB * GB
    ) -> tuple[list[str], dict[str, float]]:
        """
        Support method to get list input files and their profile
        :param path: input path
        :param max_file_size: max file size
        :param min_file_size: min file size
        :param cm_files: overwrite for the m_files in the class
        :return: tuple of file list and profile
        """
        # Get files list.
        p_list = []
        total_input_file_size = 0
        i = 0
        for file in self.arrS3.list_files(path):
            if i >= cm_files > 0:
                break
            # Only use .parquet files
            if str(file["name"]).endswith(".parquet"):
                p_list.append(str(file["name"]))
                size = file["size"]
                total_input_file_size += size
                if min_file_size > size:
                    min_file_size = size
                if max_file_size < size:
                    max_file_size = size
                i += 1
        return (
            p_list,
            {
                "max_file_size": max_file_size / MB,
                "min_file_size": min_file_size / MB,
                "total_file_size": total_input_file_size / MB,
            },
        )

    def _get_input_files(
        self,
        input_path: str,
        output_path: str,
        cm_files: int,
        max_file_size: int = 0,
        min_file_size: int = MB * GB,
    ) -> tuple[list[str], dict[str, float]]:
        """
        Get list and size of files from input path, that do not exist in the output path
        :param input_path: input path
        :param output_path: output path
        :param cm_files: max files to get
        :return: tuple of file list and and profile
        """
        if not self.checkpoint:
            return self._get_files_folder(
                path=input_path, cm_files=cm_files, min_file_size=min_file_size, max_file_size=max_file_size
            )
        pout_list, _ = self._get_files_folder(path=output_path, cm_files=-1)
        output_base_names = [file.replace(self.output_folder, self.input_folder) for file in pout_list]
        p_list = []
        total_input_file_size = 0
        i = 0
        for file in self.arrS3.list_files(input_path):
            if i >= cm_files > 0:
                break
            # Only use .parquet files
            f_name = str(file["name"])
            if f_name.endswith(".parquet") and f_name not in output_base_names:
                p_list.append(f_name)
                size = file["size"]
                total_input_file_size += size
                if min_file_size > size:
                    min_file_size = size
                if max_file_size < size:
                    max_file_size = size
                i += 1
        return (
            p_list,
            {
                "max_file_size": max_file_size / MB,
                "min_file_size": min_file_size / MB,
                "total_file_size": total_input_file_size / MB,
            },
        )

    def get_files_to_process(self) -> tuple[list[str], dict[str, float]]:
        """
        Get files to process
        :return: list of files and a dictionary of the files profile:
            "max_file_size",
            "min_file_size",
            "total_file_size"
        """
        # Check if we are using data sets
        if self.d_sets is not None:
            # get folders for the input
            folders_to_use = []
            folders = self.arrS3.list_folders(self.input_folder)
            # Only use valid folders
            for ds in self.d_sets:
                suffix = ds + "/"
                for f in folders:
                    if f.endswith(suffix):
                        folders_to_use.append(f)
                        break
            profile = {"max_file_size": 0.0, "min_file_size": 0.0, "total_file_size": 0.0}
            if len(folders_to_use) > 0:
                # if we have valid folders
                path_list = []
                max_file_size = 0
                min_file_size = MB * GB
                total_file_size = 0
                cm_files = self.m_files
                for folder in folders_to_use:
                    plist, profile = self._get_input_files(
                        input_path=self.input_folder + folder,
                        output_path=self.output_folder + folder,
                        cm_files=cm_files,
                        min_file_size=min_file_size,
                        max_file_size=max_file_size,
                    )
                    path_list += plist
                    total_file_size += profile["total_file_size"]
                    if len(path_list) >= cm_files > 0:
                        break
                    max_file_size = profile["max_file_size"] * MB
                    min_file_size = profile["min_file_size"] * MB
                    if cm_files > 0:
                        cm_files -= len(plist)
                profile["total_file_size"] = total_file_size
            else:
                path_list = []
        else:
            # Get input files list
            path_list, profile = self._get_input_files(
                input_path=self.input_folder,
                output_path=self.output_folder,
                cm_files=self.m_files,
            )
        return path_list, profile

    def get_table(self, path: str) -> pa.table:
        """
        Get pyArrow table for a given path
        :param path - file path
        :return: pyArrow table or None, if the table read failed
        """
        return self.arrS3.read_table(path)

    def get_output_location(self, path: str) -> str:
        """
        Get output location based on input
        :param path: input file location
        :return: output file location
        """
        return path.replace(self.input_folder, self.output_folder)

    def save_table(self, path: str, table: pa.Table) -> tuple[int, dict[str, Any]]:
        """
        Save table to a given location
        :param path: location to save table
        :param table: table
        :return: size of table in memory and a dictionary as
        defined https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/put_object.html
        in the case of failure dict is None
        """
        return self.arrS3.save_table(key=path, table=table)

    def save_table_with_schema(self, path: str, table: pa.Table) -> tuple[int, dict[str, Any]]:
        """
        Save table to a given location fixing schema, required for lakehouse
        :param path: location to save table
        :param table: table
        :return: size of table in memory and a dictionary as
        defined https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/put_object.html
        in the case of failure dict is None
        """
        # update schema to ensure part ids to be there
        fields = []
        columns = table.column_names
        tbl_metadata = table.schema.metadata
        for index in range(len(table.column_names)):
            field = table.field(index)
            fields.append(field.with_metadata({"PARQUET:field_id": f"{index + 1}"}))
            tbl_metadata[columns[index]] = json.dumps({"PARQUET:field_id": f"{index + 1}"}).encode()
        schema = pa.schema(fields, metadata=tbl_metadata)
        tbl = pa.Table.from_arrays(arrays=list(table.itercolumns()), schema=schema)
        return self.arrS3.save_table(key=path, table=tbl)

    def save_job_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """
        Save metadata
        :param metadata: a dictionary, containing the following keys
        (see https://github.ibm.com/arc/dmf-library/issues/158):
            "pipeline",
            "job details",
            "code",
            "job_input_params",
            "execution_stats",
            "job_output_stats"
        two additional elements:
            "source"
            "target"
        are filled bu implementation
        :return: a dictionary as
        defined https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/put_object.html
        in the case of failure dict is None
        """
        metadata["source"] = {"name": self.input_folder, "type": "path"}
        metadata["target"] = {"name": self.output_folder, "type": "path"}
        return self.save_file(path=f"{self.output_folder}metadata.json", data=json.dumps(metadata, indent=2).encode())

    def get_file(self, path: str) -> bytes:
        """
        Get file as a byte array
        :param path: file path
        :return: bytes array of file content
        """
        filedata = self.arrS3.read_file(path)
        if path.endswith("gz"):
            filedata = gzip.decompress(filedata)
        return filedata

    def get_folder_files(self, path: str, extensions: list[str]) -> list[bytes]:
        """
        Get a list of byte content of files
        :param path: file path
        :param extensions: a list of file extensions to include
        :return:
        """
        result = []
        files = self.arrS3.list_files(key=path)
        for file in files:
            f_name = str(file["name"])
            for ext in extensions:
                if f_name.endswith(ext):
                    # include the file
                    f_bytes = self.get_file(path=f_name)
                    if f_bytes is not None:
                        result.append(f_bytes)
                    break
        return result

    def save_file(self, path: str, data: bytes) -> dict[str, Any]:
        """
        Save byte array to the file
        :param path: file path
        :param data: byte array
        :return: a dictionary as
        defined https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3/client/put_object.html
        in the case of failure dict is None
        """
        return self.arrS3.save_file(key=path, data=data)