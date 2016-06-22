'''
Util functions for luigi bigmetadata tasks.
'''

from collections import OrderedDict

import os
import subprocess
import logging
import sys
import time
import re
from hashlib import sha1
from itertools import izip_longest

from slugify import slugify
import requests

from luigi import Task, Parameter, LocalTarget, Target, BooleanParameter

from sqlalchemy import Table, types, Column
from sqlalchemy.dialects.postgresql import JSON

from tasks.meta import (OBSColumn, OBSTable, metadata, Geometry,
                        OBSColumnTable, OBSTag, current_session,
                        session_commit, session_rollback)


def get_logger(name):
    '''
    Obtain a logger outputing to stderr with specified name. Defaults to INFO
    log level.
    '''
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter('%(asctime)-15s %(message)s'))
    logger.addHandler(handler)
    return logger

LOGGER = get_logger(__name__)


def remove_accents(instr):
    '''
    Remove accents from unicode characters, returns ASCII str.

    Useful in case joining against input in which unicode characters have been
    roughly replaced with ASCII equivalents
    '''
    return instr.replace(u'\xe1', 'a') \
            .replace(u'\xe9', 'e') \
            .replace(u'\xed', 'i') \
            .replace(u'\xf1', 'n') \
            .replace(u'\xf3', 'o') \
            .replace(u'\xfa', 'u')

    return instr


def shell(cmd):
    '''
    Run a shell command. Returns the STDOUT output.
    '''
    try:
        return subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT)
    except subprocess.CalledProcessError as err:
        LOGGER.error(err.output)
        raise


def underscore_slugify(txt):
    return slugify(camel_to_underscore(re.sub(
        r'[^a-zA-Z0-9]+', '_', txt))).replace('-', '_')


def classpath(obj):
    '''
    Path to this task, suitable for the current OS.
    '''
    return '.'.join(obj.__module__.split('.')[1:])


def query_cartodb(query):
    #carto_url = 'https://{}/api/v2/sql'.format(os.environ['CARTODB_DOMAIN'])
    carto_url = os.environ['CARTODB_URL'] + '/api/v2/sql'
    resp = requests.post(carto_url, data={
        'api_key': os.environ['CARTODB_API_KEY'],
        'q': query
    })
    #assert resp.status_code == 200
    #if resp.status_code != 200:
    #    raise Exception(u'Non-200 response ({}) from carto: {}'.format(
    #        resp.status_code, resp.text))
    return resp


def upload_via_ogr2ogr(outname, localname, schema):
    api_key = os.environ['CARTODB_API_KEY']
    cmd = u'''
ogr2ogr --config CARTODB_API_KEY $CARTODB_API_KEY \
        -f CartoDB "CartoDB:observatory" \
        -overwrite \
        -nlt GEOMETRY \
        -nln "{private_outname}" \
        PG:dbname=$PGDATABASE' active_schema={schema}' '{tablename}'
    '''.format(private_outname=outname, tablename=localname,
               schema=schema)
    print cmd
    shell(cmd)


def import_api(request, json_column_names=None):
    '''
    Run the import api with `request`.  Optional `json_column_names` will
    convert those columns to JSON type after the fact.
    '''
    api_key = os.environ['CARTODB_API_KEY']
    json_column_names = json_column_names or []
    resp = requests.post('{url}/api/v1/imports/?api_key={api_key}'.format(
        url=os.environ['CARTODB_URL'],
        api_key=api_key
    ), json=request)
    assert resp.status_code == 200

    import_id = resp.json()["item_queue_id"]
    while True:
        resp = requests.get('{url}/api/v1/imports/{import_id}?api_key={api_key}'.format(
            url=os.environ['CARTODB_URL'],
            import_id=import_id,
            api_key=api_key
        ))
        if resp.json()['state'] == 'complete':
            break
        elif resp.json()['state'] == 'failure':
            raise Exception('Import failed: {}'.format(resp.json()))
        print resp.json()['state']
        time.sleep(1)

    # if failing below, try reloading https://observatory.cartodb.com/dashboard/datasets
    assert resp.json()['table_name'] == request['table_name'] # the copy should not have a
                                                             # mutilated name (like '_1', '_2' etc)

    for colname in json_column_names:
        query = 'ALTER TABLE {outname} ALTER COLUMN {colname} ' \
                'SET DATA TYPE json USING {colname}::json'.format(
                    outname=resp.json()['table_name'], colname=colname
                )
        print query
        resp = query_cartodb(query)
        assert resp.status_code == 200


def sql_to_cartodb_table(outname, localname, json_column_names=None,
                         schema='observatory'):
    '''
    Move the specified table to cartodb

    If json_column_names are specified, then those columns will be altered to
    JSON after the fact (they get smushed to TEXT at some point in the import
    process)
    '''
    private_outname = outname + '_private'
    upload_via_ogr2ogr(private_outname, localname, schema)

    # populate the_geom_webmercator
    # if you try to use the import API to copy a table with the_geom populated
    # but not the_geom_webmercator, we get an error for unpopulated column
    resp = query_cartodb(
        'UPDATE {tablename} '
        'SET the_geom_webmercator = CDB_TransformToWebmercator(the_geom) '.format(
            tablename=private_outname
        )
    )
    assert resp.status_code == 200

    print 'copying via import api'
    import_api({
        'table_name': outname,
        'table_copy': private_outname,
        'create_vis': False,
        'type_guessing': False,
        'privacy': 'public'
    }, json_column_names=json_column_names)
    resp = query_cartodb('DROP TABLE "{}" CASCADE'.format(private_outname))
    assert resp.status_code == 200


class PostgresTarget(Target):
    '''
    PostgresTarget which by default uses command-line specified login.
    '''

    def __init__(self, schema, tablename):
        self._schema = schema
        self._tablename = tablename

    @property
    def table(self):
        return '"{schema}".{tablename}'.format(schema=self._schema,
                                               tablename=self._tablename)

    @property
    def tablename(self):
        return self._tablename

    @property
    def schema(self):
        return self._schema

    def exists(self):
        session = current_session()
        resp = session.execute('SELECT COUNT(*) FROM information_schema.tables '
                               "WHERE table_schema ILIKE '{schema}'  "
                               "  AND table_name ILIKE '{tablename}' ".format(
                                   schema=self._schema,
                                   tablename=self._tablename))
        return int(resp.fetchone()[0]) > 0


class CartoDBTarget(Target):
    '''
    Target which is a CartoDB table
    '''

    def __init__(self, tablename):
        self.tablename = tablename

    def __str__(self):
        return self.tablename

    def exists(self):
        resp = query_cartodb('SELECT * FROM "{tablename}" LIMIT 0'.format(
            tablename=self.tablename))
        return resp.status_code == 200

    def remove(self):
        api_key = os.environ['CARTODB_API_KEY']
        # get dataset id: GET https://observatory.cartodb.com/api/v1/tables/obs_column_table_3?api_key=bf40056ab6e223c07a7aa7731861a7bda1043241
        try:
            while True:
                resp = requests.get('{url}/api/v1/tables/{tablename}?api_key={api_key}'.format(
                    url=os.environ['CARTODB_URL'],
                    tablename=self.tablename,
                    api_key=api_key
                ))
                viz_id = resp.json()['id']
                # delete dataset by id DELETE https://observatory.cartodb.com/api/v1/viz/ed483a0b-7842-4610-9f6c-8591273b8e5c?api_key=bf40056ab6e223c07a7aa7731861a7bda1043241
                try:
                    requests.delete('{url}/api/v1/viz/{viz_id}?api_key={api_key}'.format(
                        url=os.environ['CARTODB_URL'],
                        viz_id=viz_id,
                        api_key=api_key
                    ), timeout=1)
                except requests.Timeout:
                    pass
        except ValueError as err:
            print err

        query_cartodb('DROP TABLE "{}"'.format(self.tablename))


def grouper(iterable, n, fillvalue=None):
    "Collect data into fixed-length chunks or blocks"
    # grouper('ABCDEFG', 3, 'x') --> ABC DEF Gxx
    args = [iter(iterable)] * n
    return izip_longest(fillvalue=fillvalue, *args)


class ColumnTarget(Target):
    '''
    '''

    def __init__(self, schema, name, column, task):
        self.schema = schema
        self.name = name
        self._id = '.'.join([schema, name])
        column.id = self._id
        #self._id = column.id
        self._task = task
        self._column = column

    def get(self, session):
        '''
        Return a copy of the underlying OBSColumn in the specified session.
        '''
        with session.no_autoflush:
            return session.query(OBSColumn).get(self._id)

    def update_or_create(self):
        self._column = current_session().merge(self._column)

    def exists(self):
        existing = self.get(current_session())
        new_version = float(self._column.version) or 0.0
        if existing:
            existing_version = float(existing.version)
            current_session().expunge(existing)
        else:
            existing_version = 0.0
        if existing and existing_version == new_version:
            return True
        elif existing and existing_version > new_version:
            raise Exception('Metadata version mismatch: cannot run task {task} '
                            'with ETL version ({etl}) older than what is in '
                            'DB ({db})'.format(task=self._task.task_id,
                                               etl=new_version,
                                               db=existing_version))
        return False


class TagTarget(Target):
    '''
    '''

    def __init__(self, tag, task):
        self._id = tag.id
        self._tag = tag
        self._task = task

    def get(self, session):
        '''
        Return a copy of the underlying OBSColumn in the specified session.
        '''
        with session.no_autoflush:
            return session.query(OBSTag).get(self._id)

    def update_or_create(self):
        self._tag = current_session().merge(self._tag)

    def exists(self):
        session = current_session()
        existing = self.get(session)
        new_version = float(self._tag.version) or 0.0
        if existing:
            existing_version = float(existing.version)
            current_session().expunge(existing)
        else:
            existing_version = 0.0
        if existing and existing_version == new_version:
            return True
        elif existing and existing_version > new_version:
            raise Exception('Metadata version mismatch: running tasks with '
                            'older version than what is in DB')
        return False


class TableTarget(Target):

    def __init__(self, schema, name, obs_table, columns, task):
        '''
        columns: should be an ordereddict if you want to specify columns' order
        in the table
        '''
        self._id = '.'.join([schema, name])
        obs_table.id = self._id
        obs_table.tablename = 'obs_' + sha1(underscore_slugify(self._id)).hexdigest()
        self._schema = schema
        self._name = name
        self._obs_table = obs_table
        self._obs_dict = obs_table.__dict__.copy()
        self._columns = columns
        self._task = task
        if obs_table.tablename in metadata.tables:
            self._table = metadata.tables[obs_table.tablename]
        else:
            self._table = None

    @property
    def table(self):
        return 'observatory.' + self._obs_table.tablename

    def sync(self):
        '''
        Whether this data should be synced to carto. Defaults to True.
        '''
        return True

    def exists(self):
        '''
        We always want to run this at least once, because we can always
        regenerate tabular data from scratch.
        '''
        session = current_session()
        existing = self.get(session)
        new_version = float(self._obs_table.version) or 0.0
        if existing:
            existing_version = float(existing.version)
            session.expunge(existing)
        else:
            existing_version = 0.0
        if existing and existing_version == new_version:
            resp = session.execute(
                'SELECT COUNT(*) FROM information_schema.tables '
                "WHERE table_schema = '{schema}'  "
                "  AND table_name = '{tablename}' ".format(
                    schema='observatory',
                    tablename=self._obs_table.tablename))
            return int(resp.fetchone()[0]) > 0
        elif existing and existing_version > new_version:
            raise Exception('Metadata version mismatch: running tasks with '
                            'older version than what is in DB')
        return False

    def get(self, session):
        '''
        Return a copy of the underlying OBSTable in the specified session.
        '''
        with session.no_autoflush:
            return session.query(OBSTable).get(self._id)

    def update_or_create_table(self):
        session = current_session()

        # create new local data table
        columns = []
        for colname, coltarget in self._columns.items():
            colname = colname.lower()
            col = coltarget.get(session)

            # Column info for sqlalchemy's internal metadata
            if col.type.lower() == 'geometry':
                coltype = Geometry

            # For enum type, pull keys from extra["categories"]
            elif col.type.lower().startswith('enum'):
                cats = col.extra['categories'].keys()
                coltype = types.Enum(*cats, name=col.id + '_enum')
            else:
                coltype = getattr(types, col.type.capitalize())
            columns.append(Column(colname, coltype))

        obs_table = self._obs_table
        # replace local data table
        if obs_table.id in metadata.tables:
            metadata.tables[obs_table.id].drop()
        self._table = Table(self._obs_table.tablename, metadata, *columns,
                            extend_existing=True, schema='observatory')
        self._table.drop(checkfirst=True)
        self._table.create()

    def update_or_create_metadata(self):
        session = current_session()
        select = []
        for i, colname_coltarget in enumerate(self._columns.iteritems()):
            colname, coltarget = colname_coltarget
            if coltarget._column.type.lower() == 'numeric':
                select.append('sum(case when {colname} is not null then 1 else 0 end) col{i}_notnull, '
                              'max({colname}) col{i}_max, '
                              'min({colname}) col{i}_min, '
                              'avg({colname}) col{i}_avg, '
                              'percentile_cont(0.5) within group (order by {colname}) col{i}_median, '
                              'mode() within group (order by {colname}) col{i}_mode, '
                              'stddev_pop({colname}) col{i}_stddev'.format(
                                  i=i, colname=colname.lower()))
        if select:
            stmt = 'SELECT COUNT(*) cnt, {select} FROM {output}'.format(
                select=', '.join(select), output=self.table)
            resp = session.execute(stmt)
            colinfo = dict(zip(resp.keys(), resp.fetchone()))
        else:
            colinfo = {}

        # replace metadata table
        self._obs_table = session.merge(self._obs_table)
        obs_table = self._obs_table

        obs_table = self._obs_table

        for i, colname_coltarget in enumerate(self._columns.iteritems()):
            colname, coltarget = colname_coltarget
            colname = colname.lower()
            col = coltarget.get(session)

            # Column info for obs metadata
            coltable = session.query(OBSColumnTable).filter_by(
                column_id=col.id, table_id=obs_table.id).first()
            if coltable:
                coltable_existed = True
                coltable.colname = colname
            else:
                # catch the case where a column id has changed
                coltable = session.query(OBSColumnTable).filter_by(
                    table_id=obs_table.id, colname=colname).first()
                if coltable:
                    coltable_existed = True
                    coltable.column = col
                else:
                    coltable_existed = False
                    coltable = OBSColumnTable(colname=colname, table=obs_table,
                                              column=col)
            # include analysis
            if col.type.lower() == 'numeric':
                # do not include linkage for any column that is 100% null
                stats = {
                    'count': colinfo.get('cnt'),
                    'notnull': colinfo.get('col%s_notnull' % i),
                    'max': colinfo.get('col%s_max' % i),
                    'min': colinfo.get('col%s_min' % i),
                    'avg': colinfo.get('col%s_avg' % i),
                    'median': colinfo.get('col%s_median' % i),
                    'mode': colinfo.get('col%s_mode' % i),
                    'stddev': colinfo.get('col%s_stddev' % i),
                }
                if stats['notnull'] == 0:
                    if coltable_existed:
                        session.delete(coltable)
                    elif coltable in session:
                        session.expunge(coltable)
                    continue
                for k in stats.keys():
                    if stats[k] is not None:
                        stats[k] = float(stats[k])
                coltable.extra = {
                    'stats': stats
                }
            session.add(coltable)



class ColumnsTask(Task):
    '''
    This will update-or-create columns defined in it when run
    '''

    def columns(self):
        '''
        '''
        raise NotImplementedError('Must return iterable of OBSColumns')

    def on_failure(self, ex):
        session_rollback(self, ex)
        super(ColumnsTask, self).on_failure(ex)

    def on_success(self):
        session_commit(self)

    def run(self):
        for _, coltarget in self.output().iteritems():
            coltarget.update_or_create()

    def version(self):
        return 0

    def output(self):
        output = OrderedDict({})
        session = current_session()
        already_in_session = [obj for obj in session]
        for col_key, col in self.columns().iteritems():
            if not col.version:
                col.version = self.version()
            output[col_key] = ColumnTarget(classpath(self), col.id or col_key, col, self)
        now_in_session = [obj for obj in session]
        for obj in now_in_session:
            if obj not in already_in_session:
                if obj in session:
                    session.expunge(obj)
        self._output = output
        return self._output


class TagsTask(Task):
    '''
    This will update-or-create tags defined in it when run
    '''

    def tags(self):
        '''
        '''
        raise NotImplementedError('Must return iterable of OBSTags')

    def on_failure(self, ex):
        session_rollback(self, ex)
        super(TagsTask, self).on_failure(ex)

    def on_success(self):
        session_commit(self)

    def run(self):
        for _, tagtarget in self.output().iteritems():
            tagtarget.update_or_create()

    def version(self):
        return 0

    def output(self):
        #if not hasattr(self, '_output'):
        output = {}
        for tag in self.tags():
            orig_id = tag.id
            tag.id = '.'.join([classpath(self), orig_id])
            if not tag.version:
                tag.version = self.version()
            output[orig_id] = TagTarget(tag, self)
        self._output = output
        return self._output


class TableToCarto(Task):

    force = BooleanParameter(default=False, significant=False)
    schema = Parameter(default='observatory')
    table = Parameter()
    outname = Parameter(default=None)

    def run(self):
        json_colnames = []
        table = '.'.join([self.schema, self.table])
        if table in metadata.tables:
            cols = metadata.tables[table].columns
            for colname, coldef in cols.items():
                coltype = coldef.type
                if isinstance(coltype, JSON):
                    json_colnames.append(colname)

        sql_to_cartodb_table(self.output().tablename, self.table, json_colnames,
                             schema=self.schema)
        self.force = False

    def output(self):
        if self.schema != 'observatory':
            table = '.'.join([self.schema, self.table])
        else:
            table = self.table
        if self.outname is None:
            self.outname = underscore_slugify(table)
        target = CartoDBTarget(self.outname)
        if self.force and target.exists():
            target.remove()
            self.force = False
        return target


# https://stackoverflow.com/questions/1175208/elegant-python-function-to-convert-camelcase-to-camel-case
def camel_to_underscore(name):
    s1 = re.sub('(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub('([a-z0-9])([A-Z])', r'\1_\2', s1).lower()



class DownloadUnzipTask(Task):
    '''
    Download a zip file to location {output}.zip and unzip it to the folder
    {output}.  Subclasses only need to define a `download` method.
    '''

    def download(self):
        '''
        Subclasses should override this, probably with something that uses
        shell('wget -O {output}.zip {url}')
        '''
        raise NotImplementedError('DownloadUnzipTask must define download()')

    def run(self):
        os.makedirs(self.output().path)
        self.download()
        shell('unzip -d {output} {output}.zip'.format(output=self.output().path))

    def output(self):
        return LocalTarget(os.path.join('tmp', classpath(self), self.task_id))


class TempTableTask(Task):
    '''
    A Task that generates a table that will not be referred to in metadata.
    '''

    def on_failure(self, ex):
        session_rollback(self, ex)
        super(TempTableTask, self).on_failure(ex)

    def on_success(self):
        session_commit(self)

    def output(self):
        shell("psql -c 'CREATE SCHEMA IF NOT EXISTS \"{schema}\"'".format(
            schema=classpath(self)))
        return PostgresTarget(classpath(self), self.task_id)


class Shp2TempTableTask(TempTableTask):
    '''
    A task that loads `input_shapefile` into a temporary postgres table.
    '''

    def input_shp(self):
        raise NotImplementedError("Must specify `input_shp` method")

    def run(self):
        cmd = 'PG_USE_COPY=yes PGCLIENTENCODING=latin1 ' \
                'ogr2ogr -f PostgreSQL PG:dbname=$PGDATABASE ' \
                '-t_srs "EPSG:4326" -nlt MultiPolygon -nln {table} ' \
                '-lco OVERWRITE=yes ' \
                '-lco SCHEMA={schema} -lco PRECISION=no ' \
                '\'{input}\' '.format(
                    schema=self.output().schema,
                    table=self.output().tablename,
                    input=self.input_shp())
        shell(cmd)


class LoadPostgresFromURL(TempTableTask):

    def load_from_url(self, url):
        '''
        Load psql at a URL into the database.

        Ignores tablespaces assigned in the SQL.
        '''
        shell('curl {url} | gunzip -c | grep -v default_tablespace | psql'.format(
            url=url))
        self.mark_done()

    def mark_done(self):
        session = current_session()
        session.execute('CREATE TABLE {table} ()'.format(
            table=self.output().table))


class TableTask(Task):
    '''
    A Task whose `populate` and `columns` methods should be overriden, and
    executes creating a single output table defined by its name, path, and
    defined columns.
    '''

    def version(self):
        return 0

    def on_failure(self, ex):
        session_rollback(self, ex)
        super(TableTask, self).on_failure(ex)

    def on_success(self):
        session_commit(self)

    def columns(self):
        raise NotImplementedError('Must implement columns method that returns '
                                   'a dict of ColumnTargets')

    def populate(self):
        raise NotImplementedError('Must implement populate method that '
                                   'populates the table')

    def description(self):
        return None

    def timespan(self):
        raise NotImplementedError('Must define timespan for table')

    def the_geom(self):
        geometry_columns = [(colname, coltarget) for colname, coltarget in
                            self.columns().iteritems() if coltarget._column.type.lower() == 'geometry']
        if len(geometry_columns) == 0:
            return None
        elif len(geometry_columns) == 1:
            session = current_session()
            return session.execute(
                'SELECT ST_AsText( '
                '  ST_Multi( '
                '    ST_CollectionExtract( '
                '      ST_MakeValid( '
                '        ST_SnapToGrid( '
                '          ST_Buffer( '
                '            ST_Union( '
                '              ST_MakeValid( '
                '                ST_Simplify( '
                '                  ST_SnapToGrid({geom_colname}, 0.3) '
                '                , 0) '
                '              ) '
                '            ) '
                '          , 0.3, 2) '
                '        , 0.3) '
                '      ) '
                '    , 3) '
                '  ) '
                ') the_geom '
                'FROM {output}'.format(
                    geom_colname=geometry_columns[0][0],
                    output=self.output().table
                )).fetchone()['the_geom']
        else:
            raise Exception('Having more than one geometry column in one table '
                            'could lead to problematic behavior ')

    def run(self):
        output = self.output()
        output.update_or_create_table()
        self.populate()
        output.update_or_create_metadata()
        self.create_indexes()
        output._obs_table.the_geom = self.the_geom()

    def create_indexes(self):
        session = current_session()
        for colname, coltarget in self.columns().iteritems():
            col = coltarget.get(session)
            if col.should_index():
                session.execute('CREATE INDEX ON {table} ({colname})'.format(
                    table=self.output().table, colname=colname))

    #def complete(self):
    #    for dep in self.deps():
    #        if not dep.complete():
    #            return False

    #    return super(TableTask, self).complete()

    def output(self):
        if not hasattr(self, '_columns'):
            self._columns = self.columns()

        self._output = TableTarget(classpath(self),
                                   underscore_slugify(self.task_id),
                                   OBSTable(description=self.description(),
                                            version=self.version(),
                                            timespan=self.timespan()),
                                   self._columns, self)
        return self._output


class RenameTables(Task):
    '''
    A one-time use task that renames all ID-instantiated data tables to their
    tablename.
    '''

    def run(self):
        session = current_session()
        for table in session.query(OBSTable):
            table_id = table.id
            tablename = table.tablename
            schema = '.'.join(table.id.split('.')[0:-1]).strip('"')
            table = table.id.split('.')[-1]
            resp = session.execute('SELECT COUNT(*) FROM information_schema.tables '
                                   "WHERE table_schema ILIKE '{schema}'  "
                                   "  AND table_name ILIKE '{table}' ".format(
                                       schema=schema,
                                       table=table))
            if int(resp.fetchone()[0]) > 0:
                resp = session.execute('SELECT COUNT(*) FROM information_schema.tables '
                                       "WHERE table_schema ILIKE 'observatory'  "
                                       "  AND table_name ILIKE '{table}' ".format(
                                           table=tablename))
                # new table already exists -- just drop it
                if int(resp.fetchone()[0]) > 0:
                    cmd = 'DROP TABLE {table_id} CASCADE'.format(table_id=table_id)
                    session.execute(cmd)
                else:
                    cmd = 'ALTER TABLE {old} RENAME TO {new}'.format(
                        old=table_id, new=tablename)
                    print cmd
                    session.execute(cmd)
                    cmd = 'ALTER TABLE "{schema}".{new} SET SCHEMA observatory'.format(
                        new=tablename, schema=schema)
                    print cmd
                    session.execute(cmd)
            else:
                resp = session.execute('SELECT COUNT(*) FROM information_schema.tables '
                                       "WHERE table_schema ILIKE 'public'  "
                                       "  AND table_name ILIKE '{table}' ".format(
                                           table=tablename))
                if int(resp.fetchone()[0]) > 0:
                    cmd = 'ALTER TABLE public.{new} SET SCHEMA observatory'.format(
                        new=tablename)
                    print cmd
                    session.execute(cmd)

        session.commit()
        self._complete = True

    def complete(self):
        return hasattr(self, '_complete')
