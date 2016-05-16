'''
Tasks to sync data locally to CartoDB
'''

from tasks.meta import current_session, OBSTable, Base, OBSColumn
from tasks.util import (TableToCarto, underscore_slugify, query_cartodb,
                        classpath, shell, PostgresTarget, TempTableTask)

from luigi import (WrapperTask, BooleanParameter, Parameter, Task, LocalTarget,
                   DateParameter)
from nose.tools import assert_equal
from urllib import quote_plus
from datetime import date

import requests


import os
import json
import requests

def extract_dict_a_from_b(a, b):
    return dict([(k, b[k]) for k in a.keys() if k in b.keys()])


def metatables():
    for tablename, table in Base.metadata.tables.iteritems():
        if tablename.startswith('observatory.obs_'):
            yield tablename, table


class Import(TempTableTask):
    '''
    Import a table from a CartoDB account
    '''

    username = Parameter(default='')
    subdomain = Parameter(default='observatory')
    table = Parameter()

    TYPE_MAP = {
        'string': 'TEXT',
        'number': 'NUMERIC',
        'geometry': 'GEOMETRY',
    }

    @property
    def _url(self):
        return 'https://{subdomain}.cartodb.com/{username}api/v2/sql'.format(
            username=self.username + '/' if self.username else '',
            subdomain=self.subdomain
        )

    def _query(self, **params):
        return requests.get(self._url, params=params)

    def _create_table(self):
        resp = self._query(
            q='SELECT * FROM {table} LIMIT 0'.format(table=self.table)
        )
        coltypes = dict([
            (k, self.TYPE_MAP[v['type']]) for k, v in resp.json()['fields'].iteritems()
        ])
        resp = self._query(
            q='SELECT * FROM {table} LIMIT 0'.format(table=self.table),
            format='csv'
        )
        colnames = resp.text.strip().split(',')
        columns = ', '.join(['{colname} {type}'.format(
            colname=c,
            type=coltypes[c]
        ) for c in colnames])
        stmt = 'CREATE TABLE {table} ({columns})'.format(table=self.output().table,
                                                         columns=columns)
        shell("psql -c '{stmt}'".format(stmt=stmt))

    def _load_rows(self):
        url = self._url + '?q={q}&format={format}'.format(
            q=quote_plus('SELECT * FROM {table}'.format(table=self.table)),
            format='csv'
        )
        shell(r"curl '{url}' | "
              r"psql -c '\copy {table} FROM STDIN WITH CSV HEADER'".format(
                  table=self.output().table,
                  url=url))

    def run(self):
        self._create_table()
        self._load_rows()
        shell("psql -c 'CREATE INDEX ON {table} USING gist (the_geom)'".format(
            table=self.output().table,
        ))


class SyncMetadata(WrapperTask):

    force = BooleanParameter(default=True)

    def requires(self):
        for tablename, _ in metatables():
            schema, tablename = tablename.split('.')
            yield TableToCarto(table=tablename, outname=tablename, force=self.force,
                               schema=schema)


def should_upload(table):
    '''
    Determine whether a table has any important columns.  If so, it should be
    uploaded, otherwise it should be ignored.
    '''
    # TODO this table doesn't want to upload
    if table.tablename == 'obs_ffebc3eb689edab4faa757f75ca02c65d7db7327':
        return False
    for coltable in table.columns:
        if coltable.column.weight > 0:
            return True
    return False


class SyncColumn(WrapperTask):
    '''
    Upload tables relevant to updating a particular column by keyword.
    '''
    keywords = Parameter()

    def requires(self):
        session = current_session()
        cols = session.query(OBSColumn).filter(OBSColumn.id.ilike(
            '%' + self.keywords + '%'
        ))
        if cols.count():
            for col in cols:
                for coltable in col.tables:
                    yield SyncData(exact_id=coltable.table.id)
        else:
            tables = session.query(OBSTable).filter(OBSTable.id.ilike(
                '%' + self.keywords + '%'
            ))
            if tables.count():
                for table in tables:
                    yield SyncData(exact_id=table.id)
            else:
                raise Exception('Unable to find any tables or columns with ID '
                                'that matched "{keywords}" via ILIKE'.format(
                                    keywords=self.keywords
                                ))


class SyncData(WrapperTask):
    '''
    Upload a single OBS table to cartodb by fuzzy ID
    '''
    force = BooleanParameter(default=True)
    id = Parameter(default=None)
    exact_id = Parameter(default=None)

    def requires(self):
        session = current_session()
        if self.exact_id:
            table = session.query(OBSTable).get(self.exact_id)
        elif self.id:
            table = session.query(OBSTable).filter(OBSTable.id.ilike('%' + self.id + '%')).one()
        else:
            raise Exception('Need id or exact_id for SyncData')
        return TableToCarto(table=table.tablename, force=self.force)


class SyncAllData(WrapperTask):

    force = BooleanParameter(default=False)

    def requires(self):
        tables = {}
        session = current_session()
        for table in session.query(OBSTable):
            if should_upload(table):
                tables[table.id] = table.tablename

        for table_id, tablename in tables.iteritems():
            yield TableToCarto(table=tablename, outname=tablename, force=self.force)


class GenerateStaticImage(Task):

    BASEMAP = {
        "type": "http",
        "options": {
            "urlTemplate": "https://{s}.maps.nlp.nokia.com/maptile/2.1/maptile/newest/satellite.day/{z}/{x}/{y}/256/jpg?lg=eng&token=A7tBPacePg9Mj_zghvKt9Q&app_id=KuYppsdXZznpffJsKT24",
            "subdomains": "1234",
            #"urlTemplate": "http://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}.png",
            #"subdomains": ["a", "b", "c"]
        }
    }

    #57d9408e-0351-11e6-9c12-0e787de82d45

    viz = Parameter()
    VIZ_URL = '{cartodb_url}/api/v2/viz/{{viz}}/viz.json'.format(
        cartodb_url=os.environ['CARTODB_URL'])
    MAP_URL = '{cartodb_url}/api/v1/map'.format(
        cartodb_url=os.environ['CARTODB_URL'])

    def viz_to_config(self):
        resp = requests.get(self.VIZ_URL.format(viz=self.viz))

        assert resp.status_code == 200
        data = resp.json()
        layers = []
        layers.append(self.BASEMAP)
        for data_layer in data['layers']:
            if data_layer['type'] == 'layergroup':
                for layer in data_layer['options']['layer_definition']['layers']:
                    if layer['visible'] is True:
                        layers.append({'type': 'mapnik', 'options': layer['options']})

        return {
            'layers': layers,
            'center': json.loads(data['center']),
            'bounds': data['bounds'],
            'zoom': data['zoom']
        }

    def get_named_map(self, map_config):

        config = {
            "version": "1.3.0",
            "layers": map_config
        }
        resp = requests.get(self.MAP_URL,
                            headers={'content-type':'application/json'},
                            params={'config': json.dumps(config)})
        return resp.json()

    def run(self):
        self.output().makedirs()
        config = self.viz_to_config()
        named_map = self.get_named_map(config['layers'])
        img_url = '{cartodb_url}/api/v1/map/static/center/' \
                '{layergroupid}/{zoom}/{center_lon}/{center_lat}/800/500.png'.format(
                    cartodb_url=os.environ['CARTODB_URL'],
                    layergroupid=named_map['layergroupid'],
                    zoom=config['zoom'],
                    center_lon=config['center'][0],
                    center_lat=config['center'][1]
                )
        print img_url
        shell('curl "{img_url}" > {output}'.format(img_url=img_url,
                                                   output=self.output().path))

    def output(self):
        return LocalTarget(os.path.join('catalog/source/img', self.task_id + '.png'))


class PurgeFunctions(Task):
    '''
    Purge remote functions
    '''
    pass


class PurgeMetadataTags(Task):
    '''
    Purge local metadata tables that no longer have tasks linking to them
    '''
    pass


class PurgeMetadataColumns(Task):
    '''
    Purge local metadata tables that no longer have tasks linking to them
    '''
    pass


class PurgeMetadataTables(Task):
    '''
    Purge local metadata tables that no longer have tasks linking to them
    '''
    pass


class PurgeMetadata(WrapperTask):
    '''
    Purge local metadata that no longer has tasks linking to it
    '''

    def requires(self):
        yield PurgeMetadataTags()
        yield PurgeMetadataColumns()
        yield PurgeMetadataTables()


class PurgeData(Task):
    '''
    Purge local data that no longer has tasks linking to it.
    '''
    pass


class PurgeRemoteData(Task):
    '''
    Purge remote data that is no longer available locally
    '''
    pass


class TestData(Task):
    '''
    See if a dataset has been uploaded & is in sync (at the least, has
    the same number of rows & columns as local).
    '''
    pass


class TestAllData(Task):
    '''
    See if all datasets have been uploaded & are in sync
    '''

    pass


class TestMetadata(Task):
    '''
    Make sure all metadata is uploaded & in sync
    '''

    def run(self):
        session = current_session()
        for tablename, table in metatables():
            pkey = [c.name for c in table.primary_key]

            resp = query_cartodb('select * from {tablename}'.format(
                tablename=tablename))
            for remote_row in resp.json()['rows']:
                uid = dict([
                    (k, remote_row[k]) for k in pkey
                ])
                local_vals = [unicode(v) for v in session.query(table).filter_by(**uid).one()]
                local_row = dict(zip([col.name for col in table.columns], local_vals))
                remote_row = dict([(k, unicode(v)) for k, v in remote_row.iteritems()])
                try:
                    assert_equal(local_row, extract_dict_a_from_b(local_row, remote_row))
                except Exception as err:
                    import pdb
                    pdb.set_trace()
                    print err

        self._complete = True

    def complete(self):
        return hasattr(self, '_complete') and self._complete is True


class Dump(Task):
    '''
    Dump of the entire observatory schema
    '''

    timestamp = DateParameter(default=date.today())

    def run(self):
        self.output().makedirs()
        shell('pg_dump -Fc -Z0 -x -n observatory -f {output}'.format(
            output=self.output().path))

    def output(self):
        return LocalTarget(os.path.join('tmp', classpath(self), self.task_id + '.dump'))


#class S3Dump(Task):
#    '''
#    Dump the file to S3
#    '''
#
#    timestamp = DateParameter(default=date.today())
#
#    def requires(self):
#        return Dump(timestamp=self.timestamp)
#
#    def output(self):
#        return S3Target()
