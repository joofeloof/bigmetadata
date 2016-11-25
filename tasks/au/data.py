import os
import urllib

from luigi import Task, Parameter, WrapperTask, LocalTarget
from collections import OrderedDict
from tasks.util import DownloadUnzipTask, shell, TableTask, TempTableTask, classpath, CSV2TempTableTask
from tasks.meta import current_session
from tasks.au.geo import (
    GEO_STE,
    GEOGRAPHIES, GeographyColumns, Geography, BaseParams)
from tasks.au.cols_bcp import BCPColumns


PROFILES = (
    'BCP',
)

STATES = (
    'AUST',
    'NSW',
    'Vic',
    'Qld',
    'SA',
    'WA',
    'Tas',
    'NT',
    'ACT',
    'OT',
)

TABLES = ['B01','B02','B03','B04A','B04B','B05','B06','B07','B08A','B08B','B09','B10A','B10B','B10C','B11A','B11B','B12A','B12B','B13','B14','B15','B16A','B16B','B17A','B17B','B18','B19','B20A','B20B','B21','B22A','B22B','B23A','B23B','B24','B25','B26','B27','B28','B29','B30','B31','B32','B33','B34','B35','B36','B37','B38','B39','B40A','B40B','B41A','B41B','B41C','B42A','B42B','B43A','B43B','B43C','B43D','B44A','B44B','B45A','B45B','B46',]


URL = 'http://www.censusdata.abs.gov.au/CensusOutput/copsubdatapacks.nsf/All%20docs%20by%20catNo/{year}_{profile}_{resolution}_for_{state}/$File/{year}_{profile}_{resolution}_for_{state}_{header}-header.zip'


class BaseDataParams(BaseParams):

    profile = Parameter(default='BCP')
    state = Parameter(default='AUST')
    header = Parameter(default='short')


class DownloadData(BaseDataParams, DownloadUnzipTask):

    def download(self):
        urllib.urlretrieve(url=URL.format(
                               year=self.year,
                               profile=self.profile,
                               resolution=self.resolution,
                               state=self.state,
                               header=self.header
                           ),
                           filename=self.output().path + '.zip')



class ImportData(BaseDataParams, CSV2TempTableTask):

    tablename = Parameter(default='B01')

    def requires(self):
        return DownloadData(resolution=self.resolution, profile=self.profile, state=self.state)

    def input_csv(self):
        cmd = 'find {path} -name \'{year}Census_{tablename}_{state}_{resolution}_{header}.csv\''.format(
            path=self.input().path,
            year=self.year,
            tablename=self.tablename,
            state=self.state,
            resolution=self.resolution,
            header=self.header,
            )
        path = shell(cmd)
        path = path.strip()

        return path


class ImportAllTables(BaseDataParams, WrapperTask):

    def requires(self):
        for table in TABLES:
            yield ImportData(resolution=self.resolution, state=self.state,
                            tablename=table)


class ImportAllStates(BaseDataParams, WrapperTask):

    def requires(self):
        for state in STATES:
            yield ImportAllTables(resolution=self.resolution, state=state)


class ImportAllResolutions(BaseDataParams, WrapperTask):

    def requires(self):
        for resolution in GEOGRAPHIES:
            yield ImportAllTables(resolution=resolution, state=self.state)


class ImportAll(BaseDataParams, WrapperTask):

    def requires(self):
        for resolution in GEOGRAPHIES:
            for state in STATES:
                yield ImportAllTables(resolution=resolution, state=state)


class Columns(BaseDataParams, ColumnsTask):

    def requires(self):
        requirements = {
            'sections': SectionTags(),
            'subsections': SubsectionTags(),
            'units': UnitTags(),
        }
        return requirements

    def version(self):
        return 1

    def validate_id(self, col_id):

        # ids start with V
        if not col_id.startswith('B'):
            return False

        # get numeric value
        num = col_id.split('B')[1]

        # ensure its an number
        try:
            num = int(num)
        except ValueError:
            return False

        return True


    def columns(self):
        cols = OrderedDict()
        input_ = self.input()

        subsectiontags = input_['subsections']
        unittags = input_['units']
        country = input_['sections']['au']

        # column req's from other tables
        column_reqs = {}

        filepath = "meta/Metadata_{year}_{profile}_DataPack.csv".format(year=self.year, profile, self.profile)

        session = current_session()
        with open(os.path.join(os.path.dirname(__file__),filepath)) as csv_meta_file:
            reader = csv.reader(csv_meta_file, delimiter=',', quotechar='"')

            for line in reader:
                # # skip headers
                if not line[0].startswith('B'):
                    continue

                col_id = line[1]
                col_name_en = line[2]
                denominators = line[3]
                tablename = line[4]
                col_unit = line[5]
                col_subsections = line[6]

                # validate the col_id (VXXX)
                if not self.validate_id(col_id):
                    print('*******col_id not valid', col_id)
                    continue

                # parse targets
                denominators = denominators.split('|')
                # print(denominators)
                # universes = universes.split('|')

                targets_dict = {}
                for x in denominators:
                    x = x.strip()
                    targets_dict[cols.get(x, column_reqs[x].get(session) if x in column_reqs else None)] = 'denominator'
                # for x in universes:
                #     x = x.strip()
                #     targets_dict[cols.get(x, column_reqs[x].get(session) if x in column_reqs else None)] = 'universe'
                targets_dict.pop(None, None)

                col_id = tablename+'_'+col_id

                cols[col_id] = OBSColumn(
                    id=col_id,
                    type='Numeric',
                    name=col_name_en.replace('_', ' '),
                    description ='',
                    # Ranking of importance, sometimes used to favor certain measures in auto-selection
                    # Weight of 0 will hide this column from the user.  We generally use between 0 and 10
                    weight=5,
                    aggregate='sum',
                    # Tags are our way of noting aspects of this measure like its unit, the country
                    # it's relevant to, and which section(s) of the catalog it should appear in
                    tags=[country, unittags[col_unit]],
                    targets= targets_dict
                )

                # append the rest of the subsection tags
                col_subsections = col_subsections.split('|')
                for subsection in col_subsections:
                    subsection = subsection.strip()
                    subsection_tag = subsectiontags[subsection]
                    cols[col_id].tags.append(subsection_tag)

        return cols



#####################################
# COPY TO OBSERVATORY
#####################################
class BCP(BaseParams, TableTask):

    def requires(self):
        return {
            'data': ImportAllTables(resolution=self.resolution, profile=PROFILE_BCP),
            'geo': Geography(resolution=self.resolution, year=self.year),
            'geometa': GeographyColumns(resolution=self.resolution),
            'meta': Columns(),
        }

    def timespan(self):
        return unicode(self.year)

    def columns(self):
        cols = OrderedDict()
        input_ = self.input()
        cols['region_id'] = input_['geometa']['geom_id']
        for colname, coltarget in input_['meta'].iteritems():
            # if coltarget._id.split('.')[-1].lower().startswith(self.topic.lower()):
            cols[colname] = coltarget
        return cols

    def populate(self):
        session = current_session()
        columns = self.columns()
        out_colnames = columns.keys()
        in_table = self.input()['data']
        in_colnames = [ct._id.split('.')[-1] for ct in columns.values()]
        in_colnames[0] = 'region_id'
        for i, in_c in enumerate(in_colnames):
            cmd =   "SELECT 'exists' FROM information_schema.columns " \
                    "WHERE table_schema = '{schema}' " \
                    "  AND table_name = '{tablename}' " \
                    "  AND column_name = '{colname}' " \
                    "  LIMIT 1".format(
                        schema=in_table.schema,
                        tablename=in_table.tablename.lower(),
                        colname=in_c.lower())
            # remove columns that aren't in input table
            if session.execute(cmd).fetchone() is None:
                in_colnames[i] = None
                out_colnames[i] = None
        in_colnames = [
            "CASE {ic}::TEXT WHEN '-6' THEN NULL ELSE {ic} END".format(ic=ic) for ic in in_colnames if ic is not None]
        out_colnames = [oc for oc in out_colnames if oc is not None]

        cmd =   'INSERT INTO {output} ({out_colnames}) ' \
                'SELECT {in_colnames} FROM {input} '.format(
                    output=self.output().table,
                    input=in_table.table,
                    in_colnames=', '.join(in_colnames),
                    out_colnames=', '.join(out_colnames))
        session.execute(cmd)


class AllBCPResolutions(BaseParams, WrapperTask):

    def requires(self):
        for resolution in GEOGRAPHIES:
            yield BCP(resolution=resolution, profile=PROFILE_BCP)