from base64 import b64decode
import os
import re

import osmium as o
import ogr
from shapely.wkb import loads, dumps

from osm_export_tool import GeomType

fab = o.geom.WKBFactory()
create_geom = lambda b : ogr.CreateGeometryFromWkb(bytes.fromhex(b))

epsg_4326 = ogr.osr.SpatialReference()
epsg_4326.ImportFromEPSG(4326)

def GetHumanReadable(size,precision=2):
    suffixes=['B','KB','MB','GB','TB']
    suffixIndex = 0
    while size > 1024 and suffixIndex < 4:
        suffixIndex += 1 #increment the index of the suffix
        size = size/1024.0 #apply the division
    return "%.*f%s"%(precision,size,suffixes[suffixIndex])


CLOSED_WAY_KEYS = ['aeroway','amenity','boundary','building','building:part','craft','geological','historic','landuse','leisure','military','natural','office','place','shop','sport','tourism']
CLOSED_WAY_KEYVALS = {'highway':'platform','public_transport':'platform'}
def closed_way_is_polygon(tags):
    for key in CLOSED_WAY_KEYS:
        if key in tags:
            return True
    for key, val in CLOSED_WAY_KEYVALS.items():
        if key in tags and tags[key] == val:
            return True
    return False

# can be more than one file (example: Shapefile w/ sidecars)
class File:
    def __init__(self,output_name,parts,theme):
        self.output_name = output_name
        self.parts = parts
        self.theme = theme

    @classmethod
    def shp(cls,name,theme):
        parts = [name + '.shp']
        parts.append(name + '.shx')
        parts.append(name + '.prj')
        parts.append(name + '.cpg')
        parts.append(name + '.dbf')
        return cls('shp',parts,theme)

    def size(self):
        total = 0
        for part in self.parts:
            total = total + os.path.getsize(part)
        return total

    def __str__(self):
        return '{0} {1} {2} {3}'.format(self.output_name,self.theme,','.join(self.parts),GetHumanReadable(self.size()))

    def __repr__(self):
        return self.__str__()

class Kml:
    class Layer:
        def __init__(self,driver,file_name,ogr_geom_type,theme):
            self.columns = theme.keys
            self.ds = driver.CreateDataSource(file_name + '.kml')
            self.ogr_layer = self.ds.CreateLayer(theme.name, epsg_4326, ogr_geom_type)

            if theme.osm_id:
                self.osm_id = True
                field_name = ogr.FieldDefn('osm_id', ogr.OFTInteger64)
                field_name.SetWidth(254)
                self.ogr_layer.CreateField(field_name)
            else:
                self.osm_id = False

            for column in self.columns:
                field_name = ogr.FieldDefn(column, ogr.OFTString)
                field_name.SetWidth(254)
                self.ogr_layer.CreateField(field_name)

            self.defn = self.ogr_layer.GetLayerDefn()

    def __init__(self,output_name,mapping):
        driver = ogr.GetDriverByName('KML')

        self.files = []
        self.layers = {}
        for t in mapping.themes:
            name = output_name + '_' + t.name
            # if the theme has only one geom type, don't add a suffix to the layer name.
            if t.points and not t.lines and not t.polygons:
                self.layers[(t.name,GeomType.POINT)] = Kml.Layer(driver,name,ogr.wkbPoint,t)
                self.files.append(File('kml',[name + '.kml'],t.name))
            elif not t.points and t.lines and not t.polygons:
                self.layers[(t.name,GeomType.LINE)] = Kml.Layer(driver,name,ogr.wkbLineString,t)
                self.files.append(File('kml',[name + '.kml'],t.name))
            elif not t.points and not t.lines and t.polygons:
                self.layers[(t.name,GeomType.POLYGON)] = Kml.Layer(driver,name,ogr.wkbMultiPolygon,t)
                self.files.append(File('kml',[name + '.kml'],t.name))
            else:
                if t.points:
                    self.layers[(t.name,GeomType.POINT)] = Kml.Layer(driver,name + '_points',ogr.wkbPoint,t)
                    self.files.append(File('kml',[name + '_points.kml'],t.name))
                if t.lines:
                    self.layers[(t.name,GeomType.LINE)] = Kml.Layer(driver,name + '_lines',ogr.wkbLineString,t)
                    self.files.append(File('kml',[name + '_lines.kml'],t.name))
                if t.polygons:
                    self.layers[(t.name,GeomType.POLYGON)] = Kml.Layer(driver,name + '_polygons',ogr.wkbMultiPolygon,t)
                    self.files.append(File('kml',[name + '_polygons.kml'],t.name))

    def write(self,osm_id,layer_name,geom_type,geom,tags):
        layer = self.layers[(layer_name,geom_type)]
        feature = ogr.Feature(layer.defn)
        feature.SetGeometry(geom)
        if layer.osm_id:
            feature.SetField('osm_id',osm_id)
        for col in layer.columns:
            if col in tags:
                feature.SetField(col,tags[col])
        layer.ogr_layer.CreateFeature(feature)

    def finalize(self):
        pass

class Shapefile:
    class Layer:
        def __init__(self,driver,file_name,ogr_geom_type,theme):
            def launderName(col):
                return re.sub(r'[^a-zA-Z0-9_]', '', col)[0:10]

            self.columns = theme.keys
            self.ds = driver.CreateDataSource(file_name + '.shp')
            self.ogr_layer = self.ds.CreateLayer(theme.name, epsg_4326, ogr_geom_type,options=['ENCODING=UTF-8'])

            if theme.osm_id:
                self.osm_id = True
                field_name = ogr.FieldDefn('osm_id', ogr.OFTInteger64)
                field_name.SetWidth(254)
                self.ogr_layer.CreateField(field_name)
            else:
                self.osm_id = False

            self.launderedNames = {}
            for column in self.columns:
                laundered_name = launderName(column)
                field_name = ogr.FieldDefn(laundered_name, ogr.OFTString)
                field_name.SetWidth(254)
                self.ogr_layer.CreateField(field_name)
                self.launderedNames[column] = laundered_name

            self.defn = self.ogr_layer.GetLayerDefn()

    def __init__(self,output_name,mapping):
        driver = ogr.GetDriverByName('ESRI Shapefile')

        self.files = []
        self.layers = {}
        for t in mapping.themes:
            name = output_name + '_' + t.name
            if t.points and not t.lines and not t.polygons:
                self.layers[(t.name,GeomType.POINT)] = Shapefile.Layer(driver,name,ogr.wkbPoint,t)
                self.files.append(File.shp(name,t.name))
            elif not t.points and t.lines and not t.polygons:
                self.layers[(t.name,GeomType.LINE)] = Shapefile.Layer(driver,name,ogr.wkbLineString,t)
                self.files.append(File.shp(name,t.name))
            elif not t.points and not t.lines and t.polygons:
                self.layers[(t.name,GeomType.POLYGON)] = Shapefile.Layer(driver,name,ogr.wkbMultiPolygon,t)
                self.files.append(File.shp(name,t.name))
            else:
                if t.points:
                    self.layers[(t.name,GeomType.POINT)] = Shapefile.Layer(driver,name + '_points',ogr.wkbPoint,t)
                    self.files.append(File.shp(name + '_points',t.name))
                if t.lines:
                    self.layers[(t.name,GeomType.LINE)] = Shapefile.Layer(driver,name + '_lines',ogr.wkbLineString,t)
                    self.files.append(File.shp(name + '_lines',t.name))
                if t.polygons:
                    self.layers[(t.name,GeomType.POLYGON)] = Shapefile.Layer(driver,name + '_polygons',ogr.wkbMultiPolygon,t)
                    self.files.append(File.shp(name + '_polygons',t.name))

    def write(self,osm_id,layer_name,geom_type,geom,tags):
        layer = self.layers[(layer_name,geom_type)]
        feature = ogr.Feature(layer.defn)
        feature.SetGeometry(geom)
        if layer.osm_id:
            feature.SetField('osm_id',osm_id)
        for col in layer.columns:
            if col in tags:
                feature.SetField(layer.launderedNames[col],tags[col])
        layer.ogr_layer.CreateFeature(feature)

    def finalize(self):
        pass

class Geopackage:
    class Layer:
        def __init__(self,ds,theme):
            self.ogr_layer = ds.CreateLayer(theme.name, epsg_4326, ogr.wkbUnknown,options=['SPATIAL_INDEX=NO'])

            if theme.osm_id:
                self.osm_id = True
                field_name = ogr.FieldDefn('osm_id', ogr.OFTInteger64)
                field_name.SetWidth(254)
                self.ogr_layer.CreateField(field_name)
            else:
                self.osm_id = False

            self.columns = theme.keys
            for column_name in self.columns:
                field_name = ogr.FieldDefn(column_name, ogr.OFTString)
                field_name.SetWidth(254)
                self.ogr_layer.CreateField(field_name)
            self.defn = self.ogr_layer.GetLayerDefn()

    def __init__(self,output_name,mapping):
        driver = ogr.GetDriverByName('GPKG')
        self.ds = driver.CreateDataSource(output_name + '.gpkg')
        self.ds.StartTransaction()

        self.files = [File('gpkg',[output_name + '.gpkg'],'')]
        self.layers = {}
        for theme in mapping.themes:
            layer = Geopackage.Layer(self.ds,theme)
            if theme.points:
                self.layers[(theme.name,GeomType.POINT)] = layer
            if theme.lines:
                self.layers[(theme.name,GeomType.LINE)] = layer
            if theme.polygons:
                self.layers[(theme.name,GeomType.POLYGON)] = layer

    def write(self,osm_id,layer_name,geom_type,geom,tags):
        layer = self.layers[(layer_name,geom_type)]
        feature = ogr.Feature(layer.defn)
        feature.SetGeometry(geom)
        if layer.osm_id:
            feature.SetField('osm_id',osm_id)
        for column_name in layer.columns:
            if column_name in tags:
                feature.SetField(column_name,tags[column_name])
        layer.ogr_layer.CreateFeature(feature)

    def finalize(self):
        self.ds.CommitTransaction()
        self.layers = None
        self.ds = None

# special case where each theme is a separate geopackage, for legacy reasons
class MultiGeopackage:
    class Layer:
        def __init__(self,output_name,theme):
            driver = ogr.GetDriverByName('GPKG')
            self.ds = driver.CreateDataSource(output_name + '_' + theme.name + '.gpkg')
            self.ds.StartTransaction()
            self.ogr_layer = self.ds.CreateLayer(theme.name, epsg_4326, ogr.wkbUnknown,options=['SPATIAL_INDEX=NO'])

            if theme.osm_id:
                self.osm_id = True
                field_name = ogr.FieldDefn('osm_id', ogr.OFTInteger64)
                field_name.SetWidth(254)
                self.ogr_layer.CreateField(field_name)
            else:
                self.osm_id = False

            self.columns = theme.keys
            for column_name in self.columns:
                field_name = ogr.FieldDefn(column_name, ogr.OFTString)
                field_name.SetWidth(254)
                self.ogr_layer.CreateField(field_name)
            self.defn = self.ogr_layer.GetLayerDefn()

    def __init__(self,output_name,mapping):
        self.files = []
        self.layers = {}
        for theme in mapping.themes:
            layer = MultiGeopackage.Layer(output_name, theme)
            self.files.append(File('gpkg',[output_name + '_' + theme.name + '.gpkg'],theme.name))
            if theme.points:
                self.layers[(theme.name,GeomType.POINT)] = layer
            if theme.lines:
                self.layers[(theme.name,GeomType.LINE)] = layer
            if theme.polygons:
                self.layers[(theme.name,GeomType.POLYGON)] = layer

    def write(self,osm_id,layer_name,geom_type,geom,tags):
        layer = self.layers[(layer_name,geom_type)]
        feature = ogr.Feature(layer.defn)
        feature.SetGeometry(geom)
        if layer.osm_id:
            feature.SetField('osm_id',osm_id)
        for column_name in layer.columns:
            if column_name in tags:
                feature.SetField(column_name,tags[column_name])
        layer.ogr_layer.CreateFeature(feature)

    def finalize(self):
        for k, layer in self.layers.items():
            layer.ds.CommitTransaction()
        self.layers = None

class Handler(o.SimpleHandler):
    def __init__(self,outputs,mapping,clipping_geom=None):
        super(Handler, self).__init__()
        self.outputs = outputs
        self.mapping = mapping
        self.clipping_geom = clipping_geom

    def node(self,n):
        if len(n.tags) == 0:
            return
        geom = None
        for theme in self.mapping.themes:
            if theme.matches(GeomType.POINT,n.tags):
                if not geom:
                    wkb = fab.create_point(n)
                    if self.clipping_geom:
                        sg = loads(bytes.fromhex(wkb))
                        if not self.clipping_geom.intersects(sg):
                            return
                    geom = create_geom(wkb)
                for output in self.outputs:
                    output.write(n.id,theme.name,GeomType.POINT,geom,n.tags)

    def way(self, w):
        if len(w.tags) == 0:
            return
        if w.is_closed() and closed_way_is_polygon(w.tags): # this will be handled in area()
            return
        try:
            # NOTE: it is possible this is actually a MultiLineString
            # in the case where a LineString is clipped by the clipping geom,
            # or the way is self-intersecting
            # but GDAL and QGIS seem to handle it OK.
            linestring = None
            for theme in self.mapping.themes:
                if theme.matches(GeomType.LINE,w.tags):
                    if not linestring:
                        wkb = fab.create_linestring(w)
                        if self.clipping_geom:
                            sg = loads(bytes.fromhex(wkb))
                            sg = self.clipping_geom.intersection(sg)
                            if sg.is_empty:
                                return
                            linestring = ogr.CreateGeometryFromWkb(dumps(sg))
                        else:
                            linestring = create_geom(wkb)
                    for output in self.outputs:
                        output.write(w.id,theme.name,GeomType.LINE,linestring,w.tags)
        except RuntimeError:
            print("Incomplete way: {0}".format(w.id))

    def area(self,a):
        if len(a.tags) == 0:
            return
        if not closed_way_is_polygon(a.tags):
            return
        osm_id = a.orig_id() if a.from_way() else -a.orig_id()
        try:
            multipolygon = None
            for theme in self.mapping.themes:
                if theme.matches(GeomType.POLYGON,a.tags):
                    if not multipolygon:
                        wkb = fab.create_multipolygon(a)
                        if self.clipping_geom:
                            sg = loads(bytes.fromhex(wkb))
                            sg = self.clipping_geom.intersection(sg)
                            if sg.is_empty:
                                return
                            multipolygon = ogr.CreateGeometryFromWkb(dumps(sg))
                        else:
                            multipolygon = create_geom(wkb)
                    for output in self.outputs:
                        output.write(osm_id,theme.name,GeomType.POLYGON,multipolygon,a.tags)
        except RuntimeError:
            print('Invalid area: {0}'.format(a.orig_id()))

