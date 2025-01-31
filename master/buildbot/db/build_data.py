# This file is part of Buildbot.  Buildbot is free software: you can
# redistribute it and/or modify it under the terms of the GNU General Public
# License as published by the Free Software Foundation, version 2.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE.  See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# Copyright Buildbot Team Members


from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from twisted.internet import defer
from twisted.python import deprecate
from twisted.python import versions

from buildbot.db import NULL
from buildbot.db import base
from buildbot.warnings import warn_deprecated


@dataclass
class BuildDataModel:
    buildid: int
    name: str
    length: int
    source: str
    value: bytes | None

    # For backward compatibility
    def __getitem__(self, key: str):
        warn_deprecated(
            '4.1.0',
            (
                'BuildDataConnectorComponent getBuildData, getBuildDataNoValue, and getAllBuildDataNoValues '
                'no longer return BuildData as dictionnaries. '
                'Usage of [] accessor is deprecated: please access the member directly'
            ),
        )

        if hasattr(self, key):
            return getattr(self, key)

        raise KeyError(key)


@deprecate.deprecated(versions.Version("buildbot", 4, 1, 0), BuildDataModel)
class BuildDataDict(BuildDataModel):
    pass


class BuildDataConnectorComponent(base.DBConnectorComponent):
    def _insert_race_hook(self, conn):
        # called so tests can simulate a race condition during insertion
        pass

    @defer.inlineCallbacks
    def setBuildData(self, buildid, name, value, source):
        def thd(conn):
            build_data_table = self.db.model.build_data

            update_values = {
                'value': value,
                'length': len(value),
                'source': source,
            }

            insert_values = {
                'buildid': buildid,
                'name': name,
                'value': value,
                'length': len(value),
                'source': source,
            }

            while True:
                q = build_data_table.update()
                q = q.where(
                    (build_data_table.c.buildid == buildid) & (build_data_table.c.name == name)
                )
                q = q.values(update_values)
                r = conn.execute(q)
                if r.rowcount > 0:
                    return
                r.close()

                self._insert_race_hook(conn)

                try:
                    q = build_data_table.insert().values(insert_values)
                    r = conn.execute(q)
                    return
                except (sa.exc.IntegrityError, sa.exc.ProgrammingError):
                    # there's been a competing insert, retry
                    pass

        yield self.db.pool.do(thd)

    @defer.inlineCallbacks
    def getBuildData(self, buildid, name):
        def thd(conn) -> BuildDataModel | None:
            build_data_table = self.db.model.build_data

            q = build_data_table.select().where(
                (build_data_table.c.buildid == buildid) & (build_data_table.c.name == name)
            )
            res = conn.execute(q)
            row = res.fetchone()
            if not row:
                return None
            return self._model_from_row(row, value=row.value)

        res = yield self.db.pool.do(thd)
        return res

    @defer.inlineCallbacks
    def getBuildDataNoValue(self, buildid, name):
        def thd(conn) -> BuildDataModel | None:
            build_data_table = self.db.model.build_data

            q = sa.select(
                build_data_table.c.buildid,
                build_data_table.c.name,
                build_data_table.c.length,
                build_data_table.c.source,
            )
            q = q.where((build_data_table.c.buildid == buildid) & (build_data_table.c.name == name))
            res = conn.execute(q)
            row = res.fetchone()
            if not row:
                return None
            return self._model_from_row(row, value=None)

        res = yield self.db.pool.do(thd)
        return res

    @defer.inlineCallbacks
    def getAllBuildDataNoValues(self, buildid):
        def thd(conn) -> list[BuildDataModel]:
            build_data_table = self.db.model.build_data

            q = sa.select(
                build_data_table.c.buildid,
                build_data_table.c.name,
                build_data_table.c.length,
                build_data_table.c.source,
            )
            q = q.where(build_data_table.c.buildid == buildid)

            return [self._model_from_row(row, value=None) for row in conn.execute(q).fetchall()]

        res = yield self.db.pool.do(thd)
        return res

    @defer.inlineCallbacks
    def deleteOldBuildData(self, older_than_timestamp):
        build_data = self.db.model.build_data
        builds = self.db.model.builds

        def count_build_datum(conn):
            res = conn.execute(sa.select(sa.func.count(build_data.c.id)))
            count = res.fetchone()[0]
            res.close()
            return count

        def thd(conn):
            count_before = count_build_datum(conn)

            if self.db._engine.dialect.name == 'sqlite':
                # sqlite does not support delete with a join, so for this case we use a subquery,
                # which is much slower

                q = sa.select(builds.c.id)
                q = q.where(
                    (builds.c.complete_at >= older_than_timestamp) | (builds.c.complete_at == NULL)
                )

                q = build_data.delete().where(build_data.c.buildid.notin_(q))
            else:
                q = build_data.delete()
                q = q.where(builds.c.id == build_data.c.buildid)
                q = q.where(
                    (builds.c.complete_at >= older_than_timestamp) | (builds.c.complete_at == NULL)
                )
            res = conn.execute(q)
            res.close()

            count_after = count_build_datum(conn)
            return count_before - count_after

        res = yield self.db.pool.do(thd)
        return res

    def _model_from_row(self, row, value: bytes | None):
        return BuildDataModel(
            buildid=row.buildid,
            name=row.name,
            length=row.length,
            source=row.source,
            value=value,
        )
