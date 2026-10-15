import json
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

from model.quick_entries import DAY_MS, problem_set_entries
from model.problem_set import problemSetModel
from db import ProblemSet
from server.problem_set import quick_entries

NOW = 1_800_000_000_000


def problem(psid, start=-100, end=100, **extra):
    return dict(psid=psid, name=f'Problem set {psid}', groupId=7, tag='Lab',
                config={'useSameSE': 1}, tm_start=NOW+start if isinstance(start, int) else start,
                tm_end=NOW+end, **extra)


class QuickEntryTests(unittest.TestCase):
    def test_time_boundaries_sort_and_wire_contract(self):
        data = problem_set_entries([
            problem(1, end=300), problem(2, start=0),
            problem(3, start=DAY_MS, end=DAY_MS+100),
            problem(4, start=DAY_MS+1, end=DAY_MS+100),
            problem(5, end=0), problem(6, start=200, end=100),
            problem(7, start='bad'),
        ], NOW)
        self.assertEqual([x['id'] for x in data['running']], ['2', '1'])
        self.assertEqual([x['id'] for x in data['upcoming']], ['3'])
        self.assertEqual(data['running'][0], dict(
            type='problemSet', id='2', title='Problem set 2',
            startTime=str(NOW), endTime=str(NOW+100), groupId='7',
            groupTitle=None, tag='Lab'))

    def test_multiple_section_windows_prioritize_running_and_choose_earliest_deadline(self):
        ps = problem(1)
        ps['config'] = {'useSameSE': 0}
        ps['groupInfo'] = [
            {'timeSetting': [{'tm_start': NOW+10, 'tm_end': NOW+20}]},
            {'timeSetting': [{'tm_start': NOW-10, 'tm_end': NOW+40},
                             {'tm_start': NOW-20, 'tm_end': NOW+30}]},
        ]
        data = problem_set_entries([ps], NOW)
        self.assertEqual(data['upcoming'], [])
        self.assertEqual(len(data['running']), 1)
        self.assertEqual(data['running'][0]['endTime'], str(NOW+30))

    def test_future_sections_choose_earliest_start_and_ignore_missing_times(self):
        ps = problem(1)
        ps['config'] = {}
        ps['groupInfo'] = [{'timeSetting': [
            {}, {'tm_start': None, 'tm_end': NOW+100},
            {'tm_start': NOW+40, 'tm_end': NOW+90},
            {'tm_start': NOW+10, 'tm_end': NOW+80},
        ]}]
        data = problem_set_entries([ps], NOW)
        self.assertEqual(data['upcoming'][0]['startTime'], str(NOW+10))


class QuickEntryEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_conversion_retains_schedule_configuration(self):
        model = object.__new__(problemSetModel)
        model.session = MagicMock()
        row = ProblemSet(psid=1, name='Scheduled', groupId=7,
                         tm_start=datetime.fromtimestamp((NOW-100)/1000),
                         tm_end=datetime.fromtimestamp((NOW+100)/1000),
                         config='{"useSameSE":1}',
                         groupInfo='[{"timeSetting":[]}]')
        model.session.query.return_value.filter.return_value.all.return_value = [row]
        data = model.ps_list_by_groups([7])
        self.assertEqual(data[0]['config'], {'useSameSE': 1})
        self.assertEqual(data[0]['groupInfo'], [{'timeSetting': []}])
        self.assertEqual(problem_set_entries(data, NOW)['running'][0]['id'], '1')

    async def test_guest_returns_empty_without_query(self):
        with patch('server.problem_set.problemSetModel') as model:
            response = await quick_entries({'logged': False})
        model.assert_not_called()
        self.assertEqual(json.loads(response.body)['data'], {'running': [], 'upcoming': []})

    async def test_current_groups_are_forwarded_and_db_session_is_closed(self):
        model = MagicMock()
        model.ps_get_quick_entries = AsyncMock(return_value={'running': [], 'upcoming': []})
        with patch('server.problem_set.problemSetModel', return_value=model):
            await quick_entries({'logged': True, 'groups': [7, 8]})
        model.ps_get_quick_entries.assert_awaited_once_with([7, 8])
        model.session.close.assert_called_once()

    async def test_model_preserves_group_scope_and_resolves_group_name_once(self):
        model = object.__new__(problemSetModel)
        model.session = MagicMock()
        model.ps_list_by_groups = MagicMock(return_value=[problem(1), problem(2)])
        with patch('utilsTime.getNowTime', return_value=NOW), \
                patch('model.problem_set.getGroupName', new=AsyncMock(return_value='Group 7')) as group:
            data = await model.ps_get_quick_entries([7])
        model.ps_list_by_groups.assert_called_once_with([7])
        group.assert_awaited_once_with(7)
        self.assertEqual([x['groupTitle'] for x in data['running']], ['Group 7', 'Group 7'])
