from pathlib import Path

import ee
import geemap
import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping
import geobr

import osmnx as ox
import time

import os
from dotenv import load_dotenv

load_dotenv()

PROJETO_GEE = os.getenv("MEU_PROJETO")
COD_MUNICIPIO = 2312908
PERIODO = ("2018-01-01", "2025-12-31")


PASTA = Path("dados")
PASTA.mkdir(exist_ok=True)



#conexão com o Google Earth Engine
try:
    ee.Initialize(project=PROJETO_GEE)
except Exception:
    ee.Authenticate()
    ee.Initialize(project=PROJETO_GEE)

print("Earth Engine conectado!")


#definindo os limites da cidade e dos bairros dela usando o IBGE
municipio_sobral = geobr.read_municipality(code_muni=COD_MUNICIPIO, year=2022).to_crs(4326)
municipio_sobral.to_file(PASTA / "municipio.gpkg", driver="GPKG")
municipio_geometria = municipio_sobral.geometry.iloc[0]

area_sobral_ee = ee.Geometry(mapping(municipio_geometria))

#usando o landsat para calcular a temperatura da superfície (LST) e o NDVI

def preparar_landsat(img):
    """ Função que recebe uma imagem e retira os pixels com nuvens,
    assim retornando imagens limpas. Além disso, também calcula e retorna 
    os valores de LST e NDVI da superfície. """

    qa = img.select("QA_PIXEL")      #elemento utilizado para verificar a qualidade das imagens

    #verificando as nuvens
    limpo = (
        #os bits 1, 3 e 4 devem ser 0 para passar
        qa.bitwiseAnd(1 << 1).eq(0)
        .And(qa.bitwiseAnd(1 << 3).eq(0))
        .And(qa.bitwiseAnd(1 << 4).eq(0))
    )

    #ST_B10 é a banda térmica da superfície no landsat
    lst = img.select("ST_B10").multiply(0.00341802).add(149.0).subtract(273.15).rename("LST")

    sr = img.select(["SR_B4", "SR_B5"]).multiply(0.0000275).add(-0.2)
    ndvi = sr.normalizedDifference(["SR_B5", "SR_B4"]).rename("NDVI")

    return lst.addBands(ndvi).updateMask(limpo).copyProperties(img, ["system:time_start"])

def landsat_anos():
    """ Função que analisa a mediana do LST e NDVI durante os anos
    2018 a 2025"""
    colecao = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .merge(ee.ImageCollection("LANDSAT/LC09/C02/T1_L2"))
        .filterBounds(area_sobral_ee)
        .filterDate(*PERIODO)
        .filter(ee.Filter.lt("CLOUD_COVER", 70))
        .map(preparar_landsat)
    )

    return colecao


#calculando as medianas de cada mês através dos anos de 2018 até 2025
medianas_mensais = []
base_lst_ndvi = landsat_anos()
for m in range(1, 13):
    mes = base_lst_ndvi.select("LST").filter(ee.Filter.calendarRange(m, m, "month")).median()
    medianas_mensais.append(mes.rename(f"LST_{m: 02d}"))


#calculando a media durante todos os anos (2018-2025)
anos_media = ee.ImageCollection([img.rename("LST") for img in medianas_mensais]).mean().rename("LST_ano")
imagem_completa = ee.Image.cat(medianas_mensais + [anos_media])

COLUNAS_MESES = [f"LST_{m: 02d}" for m in range (1, 13)]
BANDAS = COLUNAS_MESES + ["LST_ano"]


#utilizando o openstreetmap para mapear parques, escolas e instituições de saúde

servidores = [
    "https://overpass-api.de/api",
    "https://overpass.kumi.systems/api",
]

etiquetas = {
    "parques": {"leisure": "park"},
    "escolas": {"amenity": "school"},
    "saude": {"amenity": ["clinic", "hospital"]},
}

for nome, tags in etiquetas.items():
    try:
        time.sleep(3)
        gdf = ox.features_from_polygon(municipio_geometria, tags)
        colunas = [c for c in ["name", "geometry"] if c in gdf.columns]

        gdf[colunas].reset_index(drop=True).to_file(PASTA / "osm.gpkg", layer=nome, driver="GPKG")
        print(nome, "-", len(gdf), "elementos")
    except Exception as erro:
        print(nome, "- não encontrou ou deu erro:", erro)

