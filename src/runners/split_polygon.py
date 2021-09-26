
# LIBRARIES
import os
import pandas as pd
from siuba import group_by, ungroup, arrange, summarize, _
import numpy as np
import geopandas as gpd
from datetime import datetime

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import contextily as ctx

from shapely.geometry import box, Polygon, MultiPolygon, GeometryCollection, shape
from shapely import wkt
from shapely.ops import transform

from babelgrid import Babel

from multiprocessing.pool import Pool
from functools import partial

from loguru import logger




## FUNCTIONS

def _get_lines(update_data = False):
    """
    Get data frame of lines with count of jams per line and split number
    """ 
    logger.info('Lines')
    
    if update_data:
        # Download data from Athena
        logger.debug("Downloading lines")
        
        conn = utils.connect_athena(path='configs/athena.yaml')
        qry = f"""
            select line_wkt, count(line_wkt) as count_lines
            from spd_sdv_waze_corona.raw_sample_jams
            group by line_wkt"""
        df_lines = pd.read_sql_query(qry, conn)
        df_lines.to_csv('/home/soniame/shared/spd-sdv-omitnik-waze/corona/geo_partition/lines/line_wkt_count_202010712.csv', index=False)
    else:
        # Read current table
        logger.debug("Reading lines")
        
        path_vs = '/home/soniame/shared/spd-sdv-omitnik-waze/corona/geo_partition/lines/line_wkt_count_202010712.csv'
        logger.debug(f"From {path_vs}")
        
        df_lines = pd.read_csv(path_vs)
    
    logger.debug(f"L: {len(df_lines)}")
                     
    return(df_lines)

def _split_groups(df_lines, ng = 6):
    """
    Split lines into same density groups
    """
    size = len(df_lines)/ng
    index_split = list()
    for n in range(ng):
        new_list = [n+1]*int(size)
        index_split.extend(new_list)
    len(index_split)    
    
    df_lines['split'] = index_split
    return(df_lines)


def _line_to_coarse(line, tiles):
    
    # list with logical value of grid tiles intersection per line
    # Total length is the number of tiles
    inter_list = list()
    for tile in tiles:
        # tile geometry
        geom = tile.geometry.shapely
        # intersection of tile geometry with line
        inter_list.append(geom.intersection(wkt.loads(line)).is_empty == False)

    # wkt assigned to each line
    if sum(inter_list) == 0:
        # In case there's no intersection
        pos = None
        t_wkt = ""
    else:
        pos = np.where(inter_list)[0].tolist()[0]  
        t_wkt = tiles[pos].geometry.wkt

    result = {'line': line, 'coarse_wkt': t_wkt}        
        
    return(result)


def _create_coarse_grid(df_lines, split):
    """
    The function creates de intersection between a H3 grid tiles and the lines in 50 sample dates.
    It's split for parallelization purposes. Each split runns pero split
    """
    
    logger.info('Create coarse grid')
    
    if False:
        # Lines done previously
        prev = pd.read_csv("/home/soniame/private/projects/corona_geo_id/coarse_grid/coarse_id.csv"). \
            rename(columns = {'line':'line_wkt'})
        logger.debug(f'PL: {len(prev)}') # preview lines  

        # Elimination of lines already done
        df_merge = df_lines.merge(prev, how='left')
        df_merge = df_merge[df_merge.coarse_wkt.isnull() == True]
        logger.debug(f'Lines done: {len(df_lines) - len(df_merge)}') # new lines
    
    # Final lines
    lines = df_lines.line_wkt
    logger.debug(f'NL: {len(lines)}') # new lines
    
    # Tiles H3
    tiles = Babel('h3').polyfill(geometry, resolution = 1)
    logger.debug(f"Tiles: {len(tiles)}")
    
    # Matching lines per tile
    with Pool(10) as p:
        r = p.map(partial(_line_to_coarse, tiles = tiles), lines)
        
    df_coarse = pd.DataFrame(r)   
    logger.debug(f"UL: {df_coarse.shape[0]}") # update lines
    
    # Locallty saved - Join is made at 
    # Notebook: notebooks/katana_bounds.ipynb#Split-lines-into-grid
    path_vs = f"/home/soniame/private/projects/corona_geo_id/coarse_grid/coarse_id_new_{split}.csv"
    logger.debug(f"To {path_vs}")
    df_coarse.to_csv(path_vs, index = False)
    
    return None


def create_coarse_grid(config, h3_resolution=2):
    
    # Reading coarse grid
    df_coarse = _get_coarse_grid(). \
        rename(columns = {'line':'line_wkt'})
    # Reading distribution
    tab = pd.read_csv('/home/soniame/shared/spd-sdv-omitnik-waze/corona/geo_partition/figures/coarse_grid_distribution.csv')
    split_names = ['R2_1', 'R2_2', 'R2_3', 'R2_4', 'R2_5', 'R2_6', 'R2_7']
    
    logger.info('SPLIT 2')
    for x in [1, 2, 3, 4, 5]:
        split_n = split_names[x]
        logger.debug(f"S: {split_n}")
        # Top 6 polygons
        big_polygon = tab.sort_values(by=['lines'], ascending=False)[:6].coarse_wkt[x]
        logger.debug(big_polygon)
        # Tiles resolution 2 for polygon
        geometry = wkt.loads(big_polygon)
        tiles_r2 = Babel('h3').polyfill(geometry, resolution=h3_resolution)
        # Lines
        df_new = df_coarse[df_coarse.coarse_wkt == big_polygon]. \
            assign(split=split_n)
        # Create coarse grid
        _create_coarse_grid(df_lines = df_new, tiles = tiles_r2, split = split_n)

    return None


def _coarse_union(csv_files):
    """
    Append coarse data from csv_files
    Either from parallelization of the same polygon or 
    breaking big polygons into a smaller grid
    """
    
    df_coarse = pd.DataFrame()
    for path_file in csv_files:
        
        logger.debug(path_file)
        data_file = pd.read_csv(path_file)
        logger.debug(f"File: {len(data_file)}")
        
        df_coarse = df_coarse.append(data_file)
        logger.debug(f"Union: {len(df_coarse)}")
        
    logger.debug(df_coarse.shape)
    logger.debug(df_coarse.drop_duplicates().shape)
    
    return(df_coarse)


def _get_coarse_grid():
    
    logger.info('Get coarse grid')
    path_vs = '/home/soniame/shared/spd-sdv-omitnik-waze/corona/geo_partition/coarse_id/coarse_grid_sample_R2.csv'
    logger.debug(f'From {path_vs}')
    
    df_coarse = pd.read_csv(path_vs)
    
    logger.debug(f'L: {len(df_coarse)}')
    
    return(df_coarse)

def _get_dist_table():
    
    logger.info('Get distribution table')
    path_dist = '/home/soniame/shared/spd-sdv-omitnik-waze/corona/geo_partition/figures/coarse_grid_distribution_R.csv'
    df_dist   = pd.read_csv(path_dist)

    logger.debug(f'G: {len(df_dist)}')

    return(df_dist)
    
    
### KATANA GRID
def threshold_func(geometry, threshold_value):
    """Compares the threshold values with the polygon area"""
    return geometry.area < threshold_value


def _intersection_geometry(geometry, wkt_str, jams = None, line_result = None):

    intersection = int(geometry.intersection(wkt.loads(str(wkt_str))).is_empty == False)
    if jams == None:
        result = intersection
    if jams != None:     
        result = intersection*jams
    if line_result == True:
        if intersection > 0:
            result = {'wkt_def': wkt_str, 'geom_def': str(geometry)}
        else:
            result = {'wkt_def': wkt_str, 'geom_def': None}
        
    return(result)


def _intersection_coarse(geometry, df_dist, wkt = 'coarse_wkt_R'):
    
    df_dist = df_dist[df_dist.coarse_wkt_R != '(MISSING)']
    in_jams = [_intersection_geometry(geometry, row[wkt], row['jams']) for index, row in df_dist.iterrows()]
    in_polygons = df_dist[[x > 0 for x in in_jams]]
    
    logger.debug(f"SumTimes: {sum(in_jams)}")
    logger.debug(f"SumJams: {sum(df_dist.jams)}")
    logger.debug(f"SumCoarse: {sum(df_coarse.count_lines)}")

    
    th_coarse = round(sum(in_jams)/sum(df_coarse.count_lines), 4)

    return(th_coarse, in_polygons)


def _pool_lines( arg, geometry, wkt = 'coarse_wkt_R', jams = 'count_lines', line_result = None):
    
    idx, row = arg
    result  = _intersection_geometry(geometry, row[wkt], row[jams], line_result)
    return(result)


def _intersection_lines(df_coarse, in_polygons, geometry):
    
    df_lines = df_coarse.merge(in_polygons[['coarse_wkt_R', 'jams']], how='inner', on = 'coarse_wkt_R')
    logger.debug(f"Lin: {len(df_lines)}")
    
    with Pool(10) as p:
        times = p.map( partial(_pool_lines, geometry = geometry), 
                       [(idx, row) for idx, row in df_lines.iterrows()] )        
    logger.debug(f"SumTimes: {sum(times)}")
    logger.debug(f"SumJams: {sum(df_dist.jams)}")
    logger.debug(f"SumCoarse: {sum(df_coarse.count_lines)}")
    
    th_lines = sum(times)/sum(df_coarse.count_lines)
    
    return( round(th_lines, 4) )



def _threshold_density_func(geometry, threshold_value):
    """
    Compares in coarse grid
    """        
    
    logger.debug(f"Ar: {geometry.area}")
    
    # Coarse check
    th_coarse, in_polygons = _intersection_coarse(geometry, df_dist)
    logger.debug(f"THC: {th_coarse}") 
    
    if (th_coarse > (threshold_value)*3):
        return(False)
    if ((th_coarse <= (threshold_value)*3) & (th_coarse > threshold_value)):
        # Intersection of lines withing coarse grid
        th_lines = _intersection_lines(df_coarse, in_polygons, geometry)
        logger.debug(f"THL: {th_lines}")
        return(th_lines <= threshold_value)
    if (th_coarse <= threshold_value):
        return(True)
    
    
def katana(geometry, threshold_func, threshold_value, max_number_tiles, number_tiles=0):
    """Splits a geometry in tiles forming a grid given a threshold function and
    a maximum number of tiles.
    
    Parameters
    ----------
    geometry: Polygon or MultiPolygon
        Initial geometry
    threshold_func: function
        Calculete how many segments or km or data points exists in geometry
        Should return True or False given a threshold_value.
        Let's say you want to stop dividing polygons when you reach 10k unique segments,
        then, you should return True when the geometry has < 10k segmnets.
    threshold_value: number
        Whatever value you set as the max of the quantity you want in each tile
    number_tiles: int
        Number of tiles, defaults to 0.
    max_number_tiles: int
        Maximum number of tiles
    
    Return
    ------
    geometry: MultiPolygon
        Initial geometry divided in tiles
        
    KUDOS https://snorfalorpagus.net/blog/2016/03/13/splitting-large-polygons-for-faster-intersections/
    """
    bounds = geometry.bounds
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    
    logger.debug(f"number_tiles: {number_tiles}")
    logger.debug(f"max_number_tiles: {max_number_tiles}")
    
    # Making the cuts
    if threshold_func(geometry, threshold_value) or number_tiles == max_number_tiles:
        # either the polygon is smaller than the threshold, or the maximum
        # number of recursions has been reached
        logger.debug(f"Final Geom: {geometry.area}")
        logger.debug(f"{str(geometry)}")
        return [geometry]
    if height >= width:
        # split left to right
        a = box(bounds[0], bounds[1], bounds[2], bounds[1] + height / 2)
        b = box(bounds[0], bounds[1] + height / 2, bounds[2], bounds[3])
    else:
        # split top to bottom
        a = box(bounds[0], bounds[1], bounds[0] + width / 2, bounds[3])
        b = box(bounds[0] + width / 2, bounds[1], bounds[2], bounds[3])
    
    # Creating result
    result = []
    for d in (a, b,):
        # c is the intersection of the geometry with the new bounds
        c = geometry.intersection(d)
        logger.debug(f"c: {c}")
        # check if c is in GeometryCollection
        if not isinstance(c, GeometryCollection):
            # if not add it
            c = [c]
        for e in c:
            # check if e is in Polygon or MultiPolygon
            if isinstance(e, (Polygon, MultiPolygon)):
                # if it is then extend the result with that new geometry
                result.extend(katana(e, threshold_func, threshold_value, max_number_tiles, number_tiles + 1))
    logger.debug(f"Result: {len(result)}")
    if number_tiles > 0:
        return result
    # convert multipart into singlepart
    final_result = []
    for g in result:
        if isinstance(g, MultiPolygon):
            final_result.extend(g)
        else:
            final_result.append(g)
    return final_result
    

    
def _katana_grid(geometry, threshold_func, threshold_value, max_number_tiles, config):
    
    logger.info('Katana grid')
    logger.info(f'Run tm {cm}')
    
    result = katana(geometry, 
                    threshold_func = threshold_func, 
                    threshold_value = threshold_value, 
                    max_number_tiles = max_number_tiles)
    print(len(MultiPolygon(result).geoms))
    print(MultiPolygon(result))

    # Multipolygon ----
    grid = list()
    for polygon in MultiPolygon(result):  # same for multipolygon.geoms
        grid.append(str(polygon))

    # Export to csv ----
    outdf = gpd.GeoDataFrame(columns=['geometry'])
    outdf['geometry'] = grid
    
    path_dir = f"{config['path_s3']}/geo_partition/geo_id/{config['cm_ve']}"
    os.makedirs(path_dir, exist_ok=True)
    outdf.to_csv(f"{path_dir}/geo_grid_area_{cm}.csv",index = False)
    
    
    
## RUNNING
def create_squares(config):
    
    # Date run ----
    global cm
    cm = str(datetime.today().strftime("%Y%m%d%H%m%s"))
    print(cm)

    # Polygon geometry definition ----
    # - Latin america BID
    # polygon = 'POLYGON ((-71.19140625 -39.198205348894795, -61.962890625 -39.198205348894795, -61.962890625 -31.316101383495635, -71.19140625 -31.316101383495635, -71.19140625 -39.198205348894795))'
    polygon = 'POLYGON((-129.454 37.238,-90.781 27.311,-67.117 20.333,-68.721 17.506,-23.765 -9.114,-65.601 -60.714,-126.421 -23.479,-129.454 37.238))'
    geometry_la = wkt.loads(polygon)
    
    # Distribution table ----
    global df_dist
    df_dist = _get_dist_table()

    # Coarse grid ----
    global df_coarse
    df_coarse = _get_coarse_grid()
        
    # Running katana splits ----
    r = _katana_grid(geometry_la, _threshold_density_func, .01, 10, config)

    
def redo_squares(config):
     
    logger.debug(config)
        
    config['path_s3'] = '/home/soniame/shared/spd-sdv-omitnik-waze/corona'
        
    # Date run ----
    global cm
    cm = str(datetime.today().strftime("%Y%m%d%H%m%s"))
    logger.debug(cm)

    # Distribution table ----
    global df_dist
    df_dist = _get_dist_table()

    # Coarse grid
    global df_coarse
    df_coarse = _get_coarse_grid()
    
    # New distribution
    tab = pd.read_csv(f"{config['path_s3']}/geo_partition/dist/distribution_{config['cm_read']}.csv")
    logger.debug(f"UP: {tab.geo_id.nunique()}")

    # Polygon geometry definition
    ratio = tab \
        .sort_values('jams', ascending=False) \
        .assign(ratio = lambda x: x.jams /(sum(df_coarse.count_lines)*.01))
    logger.debug(f"Total .01 {(sum(df_coarse.count_lines)*.01)}")
    squares = ratio[ratio.ratio > config['ratio_min']]
    logger.debug(f"Redo: {len(squares)}")
    
    # Katana in each polygon
    cm_ve = cm
    config['cm_ve'] = cm
    for polygon in squares.geo_id.tolist():
        
        logger.debug(polygon)
        geometry = wkt.loads(polygon)
        cm = cm_ve + polygon

        logger.debug(cm)
        
        # Running katana splits 
        r = _katana_grid(geometry, _threshold_density_func, .01, config['max_tiles'], config)
    
    
    
def _lines_squares(square, df_coarse, df_dist):
    square = wkt.loads(str(square))
    logger.debug(f'{square}')
    
    # Hexagons intersected with square
    _, in_hex = _intersection_coarse(square, df_dist)
    logger.debug(f'Hex: {len(in_hex)}')

    # Lines inside this coarse
    df_lines = df_coarse.merge(in_hex[['coarse_wkt_R', 'jams']], how='inner', on = 'coarse_wkt_R')
    logger.debug(f"Lin: {len(df_lines)}")

    # Intersection of lines inside square
    with Pool(10) as p:
        result = p.map( partial(_pool_lines, 
                               geometry = square,
                               wkt = 'line_wkt', 
                               jams = 'jams', 
                               line_result = True), 
                       [(idx, row) for idx, row in df_lines.iterrows()] )
        
    df = pd.DataFrame(result).dropna()    
    return(df)


def find_poly(x):
    if x.endswith('.csv'):
        return(os.path.join(mypath, x))

    
def density_lines_squares(config):
    
    # Distribution table ----
    df_dist = _get_dist_table()

    # Coarse grid ----
    df_coarse = _get_coarse_grid()

    #logger.debug(config['cm_read'])
    path_s3 = '/home/soniame/shared/spd-sdv-omitnik-waze/corona' #config['s3_path']
    
    # Geo grid ----
    cm_read = config['cm_read']
    read_paths = f"{path_s3}/geo_partition/geo_id/{cm_read}"
    geo_id_paths = [os.path.join(read_paths, x) for x in os.listdir(read_paths)]
    logger.debug(f"Paths: {len(geo_id_paths)}")
    
    # Read all files in geo_id partitions
    df_squares = pd.DataFrame()
    for path in geo_id_paths:
        # read the data frame
        if path.endswith('.csv'):
            df = pd.read_csv(path)        
            df['polygon'] = path[path.find('POL'):].replace('.csv', '')
            df_squares = df_squares.append(df)
    logger.debug(f"Sh: {df_squares.shape}")    
            
    # Create directory
    path_dir = f"{path_s3}/geo_partition/geo_lines/{cm_read}"
    os.makedirs(path_dir, exist_ok=True)
    
    # Running squares splits ----
    logger.debug(f"Polygons: {len(df_squares.geometry)}")
    squares_list = df_squares.geometry.tolist()
    logger.debug(f"Squares: {len(squares_list)}")
    for i in range(len(squares_list)):
        logger.debug(f"i: {i}")
        square = squares_list[i]
        df_sq = _lines_squares(square, df_coarse, df_dist)
        #logger.debug(f"{df_sq.head()}")
        df_sq.to_csv(f'{path_dir}/results_{i}.csv', index = False)

        
def _lines_join(config):
    
    logger.info('lines to geo partition id')
    
    # paths
    cm_read = config['cm_read']
    path_dir = f"{config['path_s3']}/geo_partition/geo_lines/{cm_read}"
    
    # Paths to read 
    paths_read = [os.path.join(path_dir, x) for x in os.listdir(path_dir)]
    df_geo_lines = pd.DataFrame()

    # Concatenate results
    for path in paths_read:
        #print(path)
        if path.endswith('.csv'):
            try:
                df = pd.read_csv(path)
                df_geo_lines = df_geo_lines.append(df)
            except:  
                logger.debug("No data" )

    # Join all files
    df_geo_lines = df_geo_lines \
        .rename(columns = {'wkt_def':'line_wkt', 'geom_def':'geo_id'}) 
    df_geo_lines = df_geo_lines[['line_wkt', 'geo_id']].groupby('line_wkt').first().reset_index()  
    df_geo_lines.head()

    # Join jams per line
    df = df_geo_lines.merge(df_coarse[['line_wkt', 'count_lines']] \
                            .rename(columns = {'count_lines':'jams'}), 
                            how = 'right')

    # Write data to csv
    df[['line_wkt', 'geo_id', 'jams']].to_csv(f'{path_dir}.csv')

    return(df)

def _distribution_tab(df, config):
    
    logger.info('table')

    tab = (df
      >> group_by(_.geo_id)
      >> summarize(lines = _.line_wkt.count(), 
                   jams = _.jams.sum())
      >> ungroup()
      >> arrange("jams")
      )
    
    tab.to_csv(f"{config['path_s3']}/geo_partition/dist/distribution_{config['cm_read']}.csv", index=False)
    tab['geometry'] = gpd.GeoSeries.from_wkt(tab['geo_id'])
    tab = gpd.GeoDataFrame(tab, geometry='geometry')
    
    return(tab)


def _distribution_map(tab, config):

    logger.info('map')
    pdf_path = f"{config['path_s3']}/geo_partition/figures/map_distribution_{config['cm_read']}.pdf"
    with PdfPages(pdf_path) as pdf:
        tab = gpd.GeoDataFrame(tab, geometry='geometry')
        tab['geometry'] = gpd.GeoSeries.from_wkt(tab['geo_id'])
        tab.crs = "EPSG:4326"
        tab = tab.to_crs(epsg=3857)
        ax = tab.plot(figsize=(10, 10), alpha=0.5, edgecolor='k', 
                      column='jams',legend=True, cmap='OrRd')
        ctx.add_basemap(ax)
        pdf.savefig()  

        
def density_lines_figures(config):
    
    config['path_s3'] = '/home/soniame/shared/spd-sdv-omitnik-waze/corona'
    
    # Coarse grid ----
    global df_coarse 
    df_coarse = _get_coarse_grid()
    
    # Lines 
    df = _lines_join(config)
    
    # Table
    tab = _distribution_tab(df, config)
    
    # Map
    _distribution_map(tab, config)
    
        
def check_existence(config):

    return True

def start(config):
    
    # Date run ----
    
    globals()[config["name"]](config)
    