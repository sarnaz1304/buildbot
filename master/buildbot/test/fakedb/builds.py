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

from twisted.internet import defer

from buildbot.db.builds import BuildModel
from buildbot.test.fakedb.base import FakeDBComponent
from buildbot.test.fakedb.row import Row
from buildbot.test.util import validation
from buildbot.util import epoch2datetime


class Build(Row):
    table = "builds"

    id_column = 'id'
    foreignKeys = ('buildrequestid', 'masterid', 'workerid', 'builderid')
    required_columns = ('buildrequestid', 'masterid', 'workerid')

    def __init__(
        self,
        id=None,
        number=29,
        buildrequestid=None,
        builderid=None,
        workerid=-1,
        masterid=None,
        started_at=1304262222,
        complete_at=None,
        state_string="test",
        results=None,
    ):
        super().__init__(
            id=id,
            number=number,
            buildrequestid=buildrequestid,
            builderid=builderid,
            workerid=workerid,
            masterid=masterid,
            started_at=started_at,
            complete_at=complete_at,
            locks_duration_s=0,
            state_string=state_string,
            results=results,
        )


class BuildProperty(Row):
    table = "build_properties"

    foreignKeys = ('buildid',)
    required_columns = ('buildid',)

    def __init__(self, buildid=None, name='prop', value=42, source='fakedb'):
        super().__init__(buildid=buildid, name=name, value=value, source=source)


class FakeBuildsComponent(FakeDBComponent):
    def setUp(self):
        self.builds = {}

    def insert_test_data(self, rows):
        for row in rows:
            if isinstance(row, Build):
                build = self.builds[row.id] = row.values.copy()
                build['properties'] = {}

        for row in rows:
            if isinstance(row, BuildProperty):
                assert row.buildid in self.builds
                self.builds[row.buildid]['properties'][row.name] = (row.value, row.source)

    # component methods

    def _newId(self):
        id = 100
        while id in self.builds:
            id += 1
        return id

    def _model_from_row(self, row):
        return BuildModel(
            id=row['id'],
            number=row['number'],
            buildrequestid=row['buildrequestid'],
            builderid=row['builderid'],
            masterid=row['masterid'],
            workerid=row['workerid'],
            started_at=epoch2datetime(row['started_at']),
            complete_at=epoch2datetime(row['complete_at']),
            locks_duration_s=row["locks_duration_s"],
            state_string=row['state_string'],
            results=row['results'],
        )

    def getBuild(self, buildid) -> defer.Deferred[BuildModel | None]:
        row = self.builds.get(buildid)
        if not row:
            return defer.succeed(None)

        return defer.succeed(self._model_from_row(row))

    def getBuildByNumber(self, builderid, number) -> defer.Deferred[BuildModel | None]:
        for row in self.builds.values():
            if row['builderid'] == builderid and row['number'] == number:
                return defer.succeed(self._model_from_row(row))
        return defer.succeed(None)

    def getBuilds(
        self, builderid=None, buildrequestid=None, workerid=None, complete=None, resultSpec=None
    ) -> defer.Deferred[list[BuildModel]]:
        ret = []
        for row in self.builds.values():
            if builderid is not None and row['builderid'] != builderid:
                continue
            if buildrequestid is not None and row['buildrequestid'] != buildrequestid:
                continue
            if workerid is not None and row['workerid'] != workerid:
                continue
            if complete is not None and complete != (row['complete_at'] is not None):
                continue
            ret.append(self._model_from_row(row))
        if resultSpec is not None:
            ret = self.applyResultSpec(ret, resultSpec)
        return defer.succeed(ret)

    def addBuild(self, builderid, buildrequestid, workerid, masterid, state_string):
        validation.verifyType(self.t, 'state_string', state_string, validation.StringValidator())
        id = self._newId()
        number = (
            max([0] + [r['number'] for r in self.builds.values() if r['builderid'] == builderid])
            + 1
        )
        self.builds[id] = {
            "id": id,
            "number": number,
            "buildrequestid": buildrequestid,
            "builderid": builderid,
            "workerid": workerid,
            "masterid": masterid,
            "state_string": state_string,
            "started_at": self.reactor.seconds(),
            "locks_duration_s": 0,
            "complete_at": None,
            "results": None,
        }
        return defer.succeed((id, number))

    def setBuildStateString(self, buildid, state_string):
        validation.verifyType(self.t, 'state_string', state_string, validation.StringValidator())
        b = self.builds.get(buildid)
        if b:
            b['state_string'] = state_string
        return defer.succeed(None)

    def finishBuild(self, buildid, results):
        now = self.reactor.seconds()
        b = self.builds.get(buildid)
        if b:
            b['complete_at'] = now
            b['results'] = results
        return defer.succeed(None)

    def getBuildProperties(self, bid, resultSpec=None):
        if bid in self.builds:
            ret = [
                {"name": k, "source": v[1], "value": v[0]}
                for k, v in self.builds[bid]['properties'].items()
            ]

        if resultSpec is not None:
            ret = self.applyResultSpec(ret, resultSpec)

        ret = {v['name']: (v['value'], v['source']) for v in ret}
        return defer.succeed(ret)

    def setBuildProperty(self, bid, name, value, source):
        assert bid in self.builds
        self.builds[bid]['properties'][name] = (value, source)
        return defer.succeed(None)

    @defer.inlineCallbacks
    def getBuildsForChange(self, changeid):
        change = yield self.db.changes.getChange(changeid)
        bsets = yield self.db.buildsets.getBuildsets()
        change_ssid = change['sourcestampid']

        change_buildsetids = set(
            bset.bsid for bset in bsets if any(change_ssid == ssid for ssid in bset.sourcestamps)
        )

        breqs = yield self.db.buildrequests.getBuildRequests()
        change_breqids = [
            breq.buildrequestid for breq in breqs if breq.buildsetid in change_buildsetids
        ]

        builds = yield self.db.builds.getBuilds()
        return [build for build in builds if build.buildrequestid in change_breqids]

    def add_build_locks_duration(self, buildid, duration_s):
        b = self.builds.get(buildid)
        if b:
            b["locks_duration_s"] += duration_s
        return defer.succeed(None)
