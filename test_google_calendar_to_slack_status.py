from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pytest import mark

from google_calendar_to_slack_status import clean_events, DEFAULT_STATUS, get_key, is_within_next_event, read_value, strptime


def test_read_value():
    d = {'a': [1, 2], 'b': {'b1': [3, 4], 'b2': 5}}
    assert read_value(d, 'a') == [1, 2]
    assert read_value(d, ('b', 'b2')) == 5


def test_get_key():
    assert get_key('foo') == 'foo'
    assert get_key(('foo', 'bar', 'baz')) == 'foo'


def test_clean_events():
    event = {'foo': 1,
             'attendees': [{'qwe': [4, 5], 'self': False},
                           {'asd': [6, 7], 'self': True, 'responseStatus': 'accepted'}],
             'end': {'dateTime': '2001-02-03T20:35:00', 'baz': 3},
             'start': {'dateTime': '2001-02-03T20:00:00', 'bar': 2},
             'status': 'confirmed',
             'summary': 'My event name',
             'transparency': 'not-transparent',
             'visibility': 'not-private'}
    
    event2 = deepcopy(event)
    event2['start']['dateTime'] = '2001-02-03T20:00:02'
    event2['status'] = 'not-confirmed'  # will be filtered out

    event3 = deepcopy(event)
    event3['start']['dateTime'] = '2001-02-03T20:00:03'
    event3['transparency'] = 'transparent'  # will be filtered out

    event3_1 = deepcopy(event)
    event3_1['start']['dateTime'] = '2001-02-03T20:00:03.1'
    del event3_1['transparency']  # "transparency" will be empty ("")

    event4 = deepcopy(event)
    event4['start']['dateTime'] = '2001-02-03T20:00:04'
    event4['visibility'] = 'private'  # "summary" will be edited

    event4_1 = deepcopy(event)
    event4_1['start']['dateTime'] = '2001-02-03T20:00:04.1'
    del event4_1['visibility']  # "visibility" will be empty ("")

    event5 = deepcopy(event)
    event5['start']['dateTime'] = '2001-02-03T20:00:05'
    event5['attendees'][1]['responseStatus'] = 'not-accepted'  # will be filtered out

    events = [event, event2, event3, event3_1, event4, event4_1, event5]
    result = clean_events(events)
    expected = [{'summary': 'My event name',
                 'start': '2001-02-03T20:00:00',
                 'end': '2001-02-03T20:35:00',
                 'status': 'confirmed',
                 'transparency': 'not-transparent',
                 'visibility': 'not-private',
                 'responseStatus': 'accepted'},
                {'summary': 'My event name',
                 'start': '2001-02-03T20:00:03.1',
                 'end': '2001-02-03T20:35:00',
                 'status': 'confirmed',
                 'transparency': '',
                 'visibility': 'not-private',
                 'responseStatus': 'accepted'},
                {'summary': DEFAULT_STATUS,
                 'start': '2001-02-03T20:00:04',
                 'end': '2001-02-03T20:35:00',
                 'status': 'confirmed',
                 'transparency': 'not-transparent',
                 'visibility': 'private',
                 'responseStatus': 'accepted'},
                {'summary':'My event name',
                 'start': '2001-02-03T20:00:04.1',
                 'end': '2001-02-03T20:35:00',
                 'status': 'confirmed',
                 'transparency': 'not-transparent',
                 'visibility': '',
                 'responseStatus': 'accepted'}]
    assert result == expected


@mark.parametrize('str_time, expected', [
    ('2001-02-03T04:05:06', datetime(2001, 2, 3, 4, 5, 6)),
    ('2001-02-03T04:05:06Z', datetime(2001, 2, 3, 4, 5, 6,
                                      tzinfo=timezone.utc)),
    ('2001-02-03T04:05:06.123456', datetime(2001, 2, 3, 4, 5, 6, 123456)),
    ('2001-02-03T04:05:06.123456Z', datetime(2001, 2, 3, 4, 5, 6, 123456,
                                             tzinfo=timezone.utc)),
    ('2001-02-03T04:05:06-03:00', datetime(2001, 2, 3, 4, 5, 6,
                                           tzinfo=timezone(timedelta(hours=-3)))),
    ('2001-02-03T04:05:06+10:00', datetime(2001, 2, 3, 4, 5, 6,
                                           tzinfo=timezone(timedelta(hours=10)))),
])
def test_strptime(str_time, expected):
    result = strptime(str_time)
    assert result == expected


def test_is_within_next_event():
    next_event = {'start': '2001-02-03T12:00:00-02:00',
                  'end': '2001-02-03T12:30:00-02:00',
                  'foo': 'bar'}

    try:
        assert is_within_next_event(None, next_event, -1)
    except AssertionError as e:
        assert 'minutes_before' in e.args[0]

    # `now` is before the event
    now = datetime(2001, 2, 3, 13, 45, 0, tzinfo=timezone.utc)
    # This is: '2001-02-03T11:45:00-02:00'
    # now = datetime(2001, 2, 3, 13, 45, 0) is not allowed, because:
    # `TypeError: can't subtract offset-naive and offset-aware datetimes`
    assert not is_within_next_event(now, next_event, 14)
    assert is_within_next_event(now, next_event, 15)
    assert is_within_next_event(now, next_event, 999)

    # `now` is during the event
    now = datetime(2001, 2, 3, 14, 15, 0, tzinfo=timezone.utc)
    # This is: '2001-02-03T12:15:00-02:00'
    assert is_within_next_event(now, next_event, 0)

    # `now` is after the event
    now = datetime(2001, 2, 3, 14, 45, 0, tzinfo=timezone.utc)
    # This is: '2001-02-03T12:45:00-02:00'
    assert not is_within_next_event(now, next_event, 0)
