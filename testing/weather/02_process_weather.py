# script gets URLs of all Australian BoM weather station observations
# ... and saves them to text files

import geopandas
# import geovoronoi
import io
import json
import logging
import multiprocessing
import os
import pandas
# import matplotlib.pyplot as plt
import requests
# import shapely.ops
import struct
import zipfile

import shutil
import urllib.request
from contextlib import closing

from bs4 import BeautifulSoup
from datetime import datetime

# where to save the files
output_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), "data")

# states to include (note: no "OT" state in BoM observations)
states = ["ACT", "NSW", "NT", "QLD", "SA", "TAS", "VIC", "WA"]

# urls for each state's weather observations
base_url = "http://www.bom.gov.au/{0}/observations/{0}all.shtml"


def main():
    start_time = datetime.now()

    # get weather stations list - obs have poor coordinates
    response = urllib.request.urlopen("ftp://ftp.bom.gov.au/anon2/home/ncc/metadata/sitelists/stations.zip")
    data = io.BytesIO(response.read())

    station_file = zipfile.ZipFile(data, 'r', zipfile.ZIP_DEFLATED).read("stations.txt").decode("utf-8")
    stations = station_file.split("\r\n")

    station_list = list()

    # split fixed width file
    field_widths = (-8, -6, 41, -8, -7, 9, 10, -15, 4, 11, -9, 7)  # negative widths represent ignored fields
    format_string = " ".join('{}{}'.format(abs(fw), 'x' if fw < 0 else 's') for fw in field_widths)
    field_struct = struct.Struct(format_string)
    parser = field_struct.unpack_from
    # print('fmtstring: {!r}, recsize: {} chars'.format(fmtstring, fieldstruct.size))

    stations.pop(0)
    stations.pop(0)
    stations.pop(0)
    stations.pop(0)
    stations.pop(0)

    for station in stations:
        if len(station) > 128:
            fields = parser(bytes(station, "utf-8"))

            # convert to list
            field_list = list()

            for field in fields:
                field_list.append(field.decode("utf-8").lstrip().rstrip())

            if field_list[5] != "..":
                station_dict = dict()
                station_dict["name"] = field_list[0]
                station_dict["latitude"] = float(field_list[1])
                station_dict["longitude"] = float(field_list[2])
                station_dict["state"] = field_list[3]
                if field_list[4] != "..":
                    station_dict["altitude"] = float(field_list[4])
                station_dict["wmo"] = int(field_list[5])

                station_list.append((station_dict))

    # create geopandas dataframe of weather stations
    df = pandas.DataFrame(station_list)
    gdf = geopandas.GeoDataFrame(df, geometry=geopandas.points_from_xy(df.longitude, df.latitude), crs="EPSG:4283")

    # write to (bleh!) shapefile for QA
    gdf.to_file(os.path.join(output_path, "weather_stations"))

    logger.info("Got weather stations : {}".format(datetime.now() - start_time))
    start_time = datetime.now()

    obs_urls = list()

    for state in states:
        # get URL for web page to scrape
        input_url = base_url.format(state.lower())

        # load and parse web page
        r = requests.get(input_url)
        soup = BeautifulSoup(r.content, features="html.parser")

        # get all links
        links = soup.find_all('a', href=True)

        for link in links:
            url = link['href']

            if "/products/" in url:

                # change URL to get JSON file of weather obs
                obs_url = url.replace("/products/", "http://www.bom.gov.au/fwo/").replace(".shtml", ".json")
                # logger.info(obs_url)

                obs_urls.append(obs_url)

    with open(os.path.join(output_path, 'weather_observations_urls.txt'), 'w', newline='') as output_file:
        output_file.write("\n".join(obs_urls))

    logger.info("Got obs file list : {}".format(datetime.now() - start_time))
    start_time = datetime.now()

    # download each obs file using multiprocessing
    pool = multiprocessing.Pool(processes=12)

    num_jobs = len(obs_urls)

    results = pool.imap_unordered(run_multiprocessing, obs_urls)

    pool.close()
    pool.join()

    # get results and remove any failures
    obs_list = list()

    for result in list(results):
        if result.get("error") is not None:
            logger.warning("\t- Failed to parse {}".format(result["error"]))
        else:
            obs_list.append(result)

    logger.info("Downloaded all obs files : {}".format(datetime.now() - start_time))
    # start_time = datetime.now()

    # create geopandas dataframe of weather obs
    df = pandas.DataFrame(obs_list)
    gdf = geopandas.GeoDataFrame(df, geometry=geopandas.points_from_xy(df.lon, df.lat), crs="EPSG:4326")

    # print(gdf)

    # write to (bleh!) shapefile for QA
    gdf.to_file(os.path.join(output_path, "weather_observations"))

    return True


def run_multiprocessing(url):
    file_path = os.path.join(output_path, "obs", url.split("/")[-1])

    # try:
    obs_text = requests.get(url).text

    with open(file_path, 'w', newline='') as output_file:
        output_file.write(obs_text)

    obs_json = json.loads(obs_text)
    obs_list = obs_json["observations"]["data"]

    try:
        # default is an error fopr when there are no observations
        result = dict()
        result["error"] = "{} : No observations".format(url)

        for obs in obs_list:
            if obs["sort_order"] == 0:
                result = obs

    except Exception as ex:
        result = dict()
        result["error"] = "{} : {}".format(url, ex)
        # print(result)

    return result


if __name__ == '__main__':
    logger = logging.getLogger()

    # set logger
    log_file = os.path.abspath(__file__).replace(".py", ".log")
    logging.basicConfig(filename=log_file, level=logging.DEBUG, format="%(asctime)s %(message)s",
                        datefmt="%m/%d/%Y %I:%M:%S %p")

    # setup logger to write to screen as well as writing to log file
    # define a Handler which writes INFO messages or higher to the sys.stderr
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    # set a format which is simpler for console use
    formatter = logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s')
    # tell the handler to use this format
    console.setFormatter(formatter)
    # add the handler to the root logger
    logging.getLogger('').addHandler(console)

    logger.info("")
    logger.info("Start weather obs download")
    # psma.check_python_version(logger)

    if main():
        logger.info("Finished successfully!")
    else:
        logger.fatal("Something bad happened!")

    logger.info("")
    logger.info("-------------------------------------------------------------------------------")