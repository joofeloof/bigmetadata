#!/usr/bin/env python

'''
Bigmetadata tasks

tasks to download and create metadata
'''

import os
import requests

from collections import OrderedDict
from luigi import Task, Parameter, LocalTarget, BooleanParameter
from tasks.util import (TableTarget, shell, classpath, pg_cursor, underscore_slugify,
                        CartoDBTarget, sql_to_cartodb_table, session_scope,
                        TableTask, ColumnsTask)
from tasks.meta import BMDColumn
from tasks.us.census.tiger import GeoidColumns
from psycopg2 import ProgrammingError


class NAICSColumns(ColumnsTask):

    def columns(self):
        return {
            'industry_code': BMDColumn(type='Text',
                                       name='Six-digit NAICS Industry Code',
                                       description="6-character Industry Code (NAICS SuperSector)",
                                       weight=0),
            'industry_title': BMDColumn(type='Text',
                                        name='NAICS Industry Title',
                                        description='Title of NAICS industry',
                                        weight=0)
        }


class NAICS(TableTask):

    URL = 'http://www.bls.gov/cew/doc/titles/industry/industry_titles.csv'

    def requires(self):
        return NAICSColumns()

    def timespan(self):
        return None

    def bounds(self):
        return None

    def columns(self):
        return self.input()

    def runsession(self, session):
        table_id = self.output().get(session).id
        shell("curl '{url}' | psql -c 'COPY {output} FROM STDIN WITH CSV HEADER'".format(
            output=table_id,
            url=self.URL
        ))


class RawQCEWColumns(ColumnsTask):

    def columns(self):
        return OrderedDict([
            ("own_code", BMDColumn(
                type='Text',
                name='Ownership Code',
                description="1-character ownership code: "
                            "http://www.bls.gov/cew/doc/titles/ownership/"
                            "ownership_titles.htm", # 5 for private
                weight=0
            )),
            ("agglvl_code", BMDColumn(
                type='Text',
                name='Aggregation Level Code',
                description="2-character aggregation level code: "
                            "http://www.bls.gov/cew/doc/titles/agglevel/agglevel_titles.htm",
                weight=0
            )),
            ("size_code", BMDColumn(
                type='Text',
                description="1-character size code: "
                            "http://www.bls.gov/cew/doc/titles/size/size_titles.htm",
                name='Size code',
                weight=0
            )),
            ("year", BMDColumn(
                type='Text',
                description="4-character year",
                name='Year',
                weight=0
            )),
            ("qtr", BMDColumn(
                type='Text',
                description="1-character quarter (always A for annual)",
                name='Quarter',
                weight=0
            )),
            ("disclosure_code", BMDColumn(
                type='Text',
                description="1-character disclosure code (either ' '(blank)), or 'N' not disclosed)",
                name='Disclosure code',
                weight=0
            )),
            ("qtrly_estabs", BMDColumn(
                type='Numeric',
                description="Count of establishments for a given quarter",
                name='Establishment count',
                weight=0
            )),
            ("month1_emplvl", BMDColumn(
                type='Numeric',
                description="Employment level for the first month of a given quarter",
                name='First month employment',
                weight=0
            )),
            ("month2_emplvl", BMDColumn(
                type='Numeric',
                description="Employment level for the second month of a given quarter",
                name='Second month employment',
                weight=0
            )),
            ("month3_emplvl", BMDColumn(
                type='Numeric',
                description="Employment level for the third month of a  given quarter",
                name='Third month employment',
                weight=0
            )),
            ("total_qtrly_wages", BMDColumn(
                type='Numeric',
                description="Total wages for a given quarter",
                name='Total wages',
                weight=0
            )),
            ("taxable_qtrly_wages", BMDColumn(
                type='Numeric',
                description="Taxable wages for a given quarter",
                name='Taxable wages',
                weight=0
            )),
            ("qtrly_contributions", BMDColumn(
                type='Numeric',
                description="Quarterly contributions for a given quarter",
                name='Total contributions',
                weight=0
            )),
            ("avg_wkly_wage", BMDColumn(
                type='Numeric',
                description="Average weekly wage for a given quarter",
                name='Average weekly wage',
                weight=0
            )),
            ("lq_disclosure_code", BMDColumn(
                type='Text',
                description="1-character location-quotient disclosure code "
                            "(either ' '(blank)), or 'N' not disclosed",
                name='Location quotient disclosure code',
                weight=0
            )),
            ("lq_qtrly_estabs", BMDColumn(
                type='Numeric',
                description="Location quotient of the quarterly establishment "
                            "count relative to the U.S. (Rounded to hundredths place)",
                name='Location quotient',
                weight=0
            )),
            ("lq_month1_emplvl", BMDColumn(
                type='Numeric',
                description="Location quotient of the employment level for the "
                            "first month of a given quarter relative to the "
                            "U.S. (Rounded to hundredths place)),",
                name="Location quotient first month",
                weight=0
            )),
            ("lq_month2_emplvl", BMDColumn(
                type='Numeric',
                description="Location quotient of the employment level for the "
                            "second month of a given quarter relative to the "
                            "U.S. (Rounded to hundredths place)),",
                name="Location quotient second month",
                weight=0
            )),
            ("lq_month3_emplvl", BMDColumn(
                type='Numeric',
                description="Location quotient of the employment level for the "
                            "third month of a given quarter relative to the "
                            "U.S. (Rounded to hundredths place)),",
                name="Location quotient third month",
                weight=0
            )),
            ("lq_total_qtrly_wages", BMDColumn(
                type='Numeric',
                description="Location quotient of the total wages for a given "
                            "quarter relative to the U.S. (Rounded to hundredths place)",
                name="Location quotient quarterly",
                weight=0
            )),
            ("lq_taxable_qtrly_wages", BMDColumn(
                type='Numeric',
                description="Location quotient of the total taxable wages for "
                            "a given quarter relative to the U.S. (Rounded to hundredths "
                            "place)",
                weight=0,
                name="Quarterly location quotient taxable wages"
            )),
            ("lq_qtrly_contributions", BMDColumn(
                type='Numeric',
                description="Location quotient of the total contributions for "
                            "a given quarter relative to the U.S. (Rounded to "
                            "hundredths place)",
                weight=0,
                name="Quarterly location quotient contributions"
            )),
            ("lq_avg_wkly_wage", BMDColumn(
                type='Numeric',
                description="Location quotient of the average weekly wage for "
                            "a given quarter relative to the U.S. (Rounded to "
                            "hundredths place)",
                weight=0,
                name="Quarterly location quotient weekly wage"
            )),
            ("oty_disclosure_code", BMDColumn(
                type='Text',
                description="1-character over-the-year disclosure code (either "
                            "' '(blank)), or 'N' not disclosed)",
                weight=0,
                name="Over-the-year Disclosure code"
            )),
            ("oty_qtrly_estabs_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year change in the count of "
                            "establishments for a given quarter",
                weight=0,
                name="Over-the-year change in establishment count"
            )),
            ("oty_qtrly_estabs_pct_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year percent change in the count of "
                            "establishments for a given quarter (Rounded to "
                            "the tenths place)",
                weight=0,
                name="Over-the-year percent change in establishment count"
            )),
            ("oty_month1_emplvl_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year change in the first month's "
                            "employment level of a given quarter",
                weight=0,
                name="Over-the-year change in first month employment level"
            )),
            ("oty_month1_emplvl_pct_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year percent change in the first month's "
                            "employment level of a given quarter (Rounded to "
                            "the tenths place)),",
                weight=0,
                name="Over-the-year percent change in first month employment level"
            )),
            ("oty_month2_emplvl_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year change in the second month's "
                            "employment level of a given quarter",
                weight=0,
                name="Over-the-year change in second month employment level"
            )),
            ("oty_month2_emplvl_pct_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year percent change in the second "
                            "month's employment level of a given quarter "
                            "(Rounded to the tenths place)",
                weight=0,
                name="Over-the-year percent change in second month employment level"
            )),
            ("oty_month3_emplvl_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year change in the third month's "
                            "employment level of a given quarter",
                weight=0,
                name="Over-the-year change in third month employment level"
            )),
            ("oty_month3_emplvl_pct_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year percent change in the third month's "
                            "employment level of a given quarter (Rounded to "
                            "the tenths place)",
                weight=0,
                name="Over-the-year percent change in third month employment level"
            )),
            ("oty_total_qtrly_wages_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year change in total quarterly wages for a given quarter",
                weight=0,
                name="Over-the-year change in total quarterly wages"
            )),
            ("oty_total_qtrly_wages_pct_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year percent change in total quarterly "
                            "wages for a given quarter (Rounded to the tenths place)",
                weight=0,
                name="Over-the-year percent change in total quarterly wages"
            )),
            ("oty_taxable_qtrly_wages_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year change in taxable quarterly wages "
                            "for a given quarter",
                weight=0,
                name="Over-the-year change in taxable quarterly wages"
            )),
            ("oty_taxable_qtrly_wages_pct_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year percent change in taxable quarterly "
                            "wages for a given quarter (Rounded to the tenths "
                            "place)",
                weight=0,
                name="Over-the-year percent change in taxable quarterly wages"
            )),
            ("oty_qtrly_contributions_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year change in quarterly contributions "
                            "for a given quarter",
                weight=0,
                name="Over-the-year change in quarterly contributions"
            )),
            ("oty_qtrly_contributions_pct_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year percent change in quarterly "
                            "contributions for a given quarter (Rounded to the "
                            "tenths place)",
                weight=0,
                name="Over-the-year percent change in quarterly contributions"
            )),
            ("oty_avg_wkly_wage_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year change in average weekly wage for a "
                            "given quarter",
                weight=0,
                name="Over-the-year change in average weekly wage"
            )),
            ("oty_avg_wkly_wage_pct_chg", BMDColumn(
                type='Numeric',
                description="Over-the-year percent change in average weekly "
                            "wage for a given quarter (Rounded to the tenths "
                            "place)",
                weight=0,
                name="Over-the-year percent change in average weekly wage"
            ))
        ])

class DownloadQCEW(Task):

    year = Parameter()
    URL = 'http://www.bls.gov/cew/data/files/{year}/csv/{year}_qtrly_singlefile.zip'

    def run(self):
        self.output().makedirs()
        shell('wget \'{url}\' -O {output}.zip'.format(
            url=self.URL.format(year=self.year), output=self.output().path))
        shell('unzip -o {output}.zip -d $(dirname {output})'.format(output=self.output().path))
        shell('mv $(dirname {output})/*.csv {output}'.format(output=self.output().path))

    def output(self):
        return LocalTarget(path=os.path.join(
            classpath(self), self.year, 'qcew.csv'))


class RawQCEW(TableTask):

    year = Parameter()

    def timespan(self):
        return self.year

    def bounds(self):
        return None

    def requires(self):
        return {
            'data': DownloadQCEW(year=self.year),
            'metadata': RawQCEWColumns(),
            'geoids': GeoidColumns(),
            'naics': NAICSColumns()
        }

    def columns(self):
        columns = OrderedDict()
        columns['county_fips'] = self.input()['geoids']['county_geoid']
        qcew_columns = self.input()['metadata']
        columns['own_code'] = qcew_columns.pop('own_code')
        columns['naics_industry_code'] = self.input()['naics']['industry_code']
        columns.update(qcew_columns)
        return columns

    def runsession(self, session):
        shell("psql -c '\\copy {table} FROM {input} WITH CSV HEADER'".format(
            table=self.output().get(session).id, input=self.input()['data'].path))


class SimpleQCEWColumns(ColumnsTask):

    def requires(self):
        return RawQCEWColumns()

    def columns(self):

        pass
        #yield Column('area_fips', Text)
        #dimensions = ('avg_wkly_wage', 'qtrly_estabs', 'month3_emplvl',
        #              'lq_avg_wkly_wage', 'lq_qtrly_estabs', 'lq_month3_emplvl')
        #naics = self.input()['naics']
        #qcew = self.input()['qcew']
        #code_to_name = dict([(code, category) for code, category in naics.select().execute()])
        #cursor = pg_cursor()
        ## TODO implement shared column on industry_code
        #cursor.execute('SELECT DISTINCT {code} FROM {qcew} ORDER BY {code} ASC'.format(
        #    code=naics_industry_code().name, qcew=qcew))
        #for code, in cursor:
        #    name = code_to_name[code]
        #    for dim in dimensions:
        #        column = Column(dim + '_' + slug_column(name), Integer, info={
        #            'code': code,
        #            'dimension': dim,
        #            'description': '{dim} for {name}'.format(
        #                dim=qcew.table.columns[dim].info['description'],
        #                name=name
        #            )
        #        })
        #        yield column


class SimpleQCEW(TableTask):
    '''
    Isolate the rows of QCEW we actually care about without significantly
    modifying the schema.  Brings us down to one quarter.

    We pull out private employment at the county level, divided by three-digit
    NAICS code and supercategory (four-digits, but simpler).

    agglvl 75: 3-digit by ownership
    agglvl 73: superlevel by ownership
    '''
    year = Parameter()
    qtr = Parameter()

    def timespan(self):
        return '{year}Q{quarter}'.format(year=self.year,
                                         quarter=self.qtr)

    def bounds(self):
        return None

    def columns(self):
        return RawQCEW(self.year).columns()

    def requires(self):
        return {
            'metadata': SimpleQCEWColumns(),
            'data': RawQCEW(self.year)
        }

    def run(self):
        cursor = pg_cursor()
        cursor.execute('CREATE TABLE {output} AS '
                       'SELECT * FROM {qcew} '
                       "WHERE agglvl_code IN ('75', '73')"
                       "      AND year = '{year}'"
                       "      AND qtr = '{qtr}'"
                       "      AND own_code = '5'".format(
                           qtr=self.qtr,
                           year=self.year,
                           output=self.output(),
                           qcew=self.input()))
        cursor.connection.commit()

    def output(self):
        return TableTarget(self, self.columns())


class QCEW(TableTask):
    '''
    Turn QCEW data into a columnar format that works better for upload
    '''

    year = Parameter()
    qtr = Parameter()

    def requires(self):
        return {
            'qcew': SimpleQCEW(year=self.year, qtr=self.qtr),
            'naics': NAICS()
        }


    def runsession(self, session):
        session.execute('INSERT INTO {output} (area_fips) '
                        'SELECT distinct area_fips FROM {qcew} '.format(
                            output=self.output(),
                            qcew=self.input()['qcew']
                        ))
        for col in self.output().table.columns:
            if 'code' not in col.info:
                continue
            query = ('UPDATE {output} SET {column} = {dim} '
                     'FROM {qcew} '
                     'WHERE {industry_code} = \'{code}\' AND '
                     '{qcew}.area_fips = {output}.area_fips'.format(
                         code=col.info['code'],
                         dim=col.info['dimension'],
                         output=self.output(),
                         column=col.name,
                         qcew=self.input()['qcew'],
                         industry_code=naics_industry_code().name
                     ), )[0]
            session.execute(query)


class ExportQCEW(Task):

    year = Parameter()
    qtr = Parameter()
    force = BooleanParameter(default=False)

    def requires(self):
        return {
            'qcew': QCEW(year=self.year, qtr=self.qtr)
        }

    def run(self):
        query = 'SELECT geoid, qcew.* FROM ' \
                '{qcew} qcew, {tiger} tiger ' \
                'WHERE tiger.geoid = qcew.area_fips'.format(
                    qcew=self.input()['qcew'],
                    tiger='tiger2013.county'
                )
        sql_to_cartodb_table(self.output(), query)

    def output(self):
        target = CartoDBTarget(slug_column(str(self.input()['qcew'])))
        if self.force and target.exists():
            target.remove()
        return target
