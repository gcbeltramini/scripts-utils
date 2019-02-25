# Description about what this script does
# =============================================================================
# 1. Get the next confirmed event in Google Calendar;
# 2. Change the status on Slack if the time when the script ran is within
#    `MINUTES_BEFORE` minutes before the start of the event until the end of
#    the event;
#    2.1 Skip events that are not confirmed, or the user is "Available" (not
#        "Busy"), or were not accepted (not "Yes", i.e., "Maybe" or "No");
# 3. If the event is private, use the message `DEFAULT_STATUS`. Otherwise, use
#    the name of the event;
# 4. The expiration time of the Slack status is when the event finishes.


# Instructions
# =============================================================================
# To run this every 5 minutes (same value as `MINUTES_BEFORE`) on workdays:
# 1. In the terminal: EDITOR=vim crontab -e
# 2. Insert: */5 * * * 1-5 . /path/to/file/where/SLACK_TOKEN/is; /path/to/python3 /path/to/file/google_calendar_to_slack_status.py
# 3. Check in the terminal: crontab -l


# Logging
# =============================================================================
import logging
import os.path

DEBUG = False  # change this to debug the script

if DEBUG:
    log_level = logging.DEBUG
    log_file = 'google_calendar_to_slack_status-debug.log'
else:
    log_level = logging.INFO
    log_file = 'google_calendar_to_slack_status.log'

LOG_FILE = os.path.join(os.path.dirname(__file__), log_file)
logging.basicConfig(filename=LOG_FILE, level=log_level)

logging.getLogger('googleapiclient.discovery_cache').setLevel(logging.ERROR)
# https://github.com/googleapis/google-api-python-client/issues/299#issuecomment-255793971

logger = logging.getLogger(__name__)


# Google Calendar functions
# =============================================================================

# Strongly inspired by: https://developers.google.com/calendar/quickstart/python

from datetime import datetime
import pytz

from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request

import pickle
import re
from typing import List, Tuple, Union


DEFAULT_STATUS = 'busy'
MINUTES_BEFORE = 5.0
CREDENTIALS_FILE = os.path.expanduser('~/.credentials/google-credentials.json')
TOKEN_FILE = os.path.expanduser('~/.credentials/google-token.pickle')

# If modifying these scopes, delete `TOKEN_FILE`
SCOPES = ('https://www.googleapis.com/auth/calendar.readonly',)


def get_credentials(token_file: str = TOKEN_FILE,
                    credentials_file: str = CREDENTIALS_FILE,
                    scopes: Tuple[str] = SCOPES):
    """
    Get Google credentials. If `token_file` doesn't exist, use
    `credentials_file` to create it when the authorization flow completes for
    the first time.
    
    Parameters
    ----------
    token_file : str, optional
        Stores the user's access and refresh tokens.
    credentials_file : str, optional
        Used only if `token_file` doesn't exist.
    scopes : tuple[str], optional
        Scopes.
    
    Returns
    -------
    creds : google.oauth2.credentials.Credentials
    """
    creds = None
    
    if os.path.exists(token_file):
        with open(token_file, 'rb') as token:
            creds = pickle.load(token)

    # If there are no (valid) credentials available, let the user log in.
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file,
                                                             scopes)
            creds = flow.run_local_server()

        # Save the credentials for the next run
        with open(token_file, 'wb') as token:
            pickle.dump(creds, token)
    
    return creds


def get_calendar():
    creds = get_credentials()
    return build('calendar', 'v3', credentials=creds)


def get_next_events(min_time: str, n: int, service) -> List[str]:
    """
    Get the next events in Google Calendar.

    Parameters
    ----------
    min_time : str
        When to start to look for an event.
    n : int
        How many events during or after `min_time`.
    service
        The output of `googleapiclient.discovery.build('calendar', ...)`

    Returns
    -------
    List of the next events, ordered by start time.
    """
    events_resource = service.events()

    logging.info(f'Getting the upcoming {n} event{"s" if n > 1 else ""}...')
    events_result = events_resource.list(calendarId='primary',
                                         timeMin=min_time,
                                         maxResults=n,
                                         singleEvents=True,
                                         orderBy='startTime').execute()
    events = events_result.get('items', [])

    if not events:
        logging.info('No upcoming events found.')
    for event in events:
        start = event['start'].get('dateTime', event['start'].get('date'))
        logging.info(f'Next event = {start} {event["summary"]}')

    logging.debug(f'Next events (full) = {events}')
    logging.debug(f'Keys of first event = {sorted(events[0].keys()) if events else []}')
    
    return events


def read_value(d: dict, key: Union[tuple, str]):
    """
    Read value from dictionary.

    Parameters
    ----------
    d : dict
    key : tuple, str
        Key (str) or nested key (tuple).

    Returns
    -------
    Value from the chosen key in `d`.
    """
    if isinstance(key, str):
        key = (key,)
    if len(key) == 1:
        return d.get(key[0], '')
    else:
        return read_value(d.get(key[0], {}), key[1::])


def get_key(keyname: Union[tuple, str]) -> str:
    return keyname if isinstance(keyname, str) else keyname[0]


def get_my_response_status(event: dict, attendees_key: str, response_key: str):
    return [aa.get(response_key, '')
            for aa in event[attendees_key]
            if aa.get('self', False)][0]


def clean_events(events: List[dict], keys=('summary',
                                          ('start', 'dateTime'),
                                          ('end', 'dateTime'),
                                          'status',
                                          'transparency',
                                          'visibility',
                                          'attendees')) -> List[dict]:
    """
    Clean list of events.
    
    Parameters
    ----------
    events: list[dict]
    keys: tuple
        Keys to look in `events`.
    
    Returns
    -------
    Filtered and cleaned list of events.
    
    References
    ----------
    - https://developers.google.com/calendar/v3/reference/events
    """

    attendees_key: str = 'attendees'
    response_key: str = 'responseStatus'

    events_clean: list = []
    for ee in events:

        # Discard free events
        # Possible values:
        # - "opaque": event blocks time on the calendar; "Show me as" = "Busy"
        # - "transparent": event does not block time on the calendar; "Show me as" = "Available"
        #
        # Keep confirmed events
        # Possible values: "confirmed", "tentative", "cancelled"
        if (ee.get('transparency', '') == 'transparent') or (ee['status'] != 'confirmed'):
            continue

        ee_clean = {get_key(kk): read_value(ee, kk) for kk in keys}

        # Keep accepted events
        # Possible values:
        # - "needsAction": didn't respond
        # - "declined": No
        # - "tentative": Maybe
        # - "accepted": Yes
        my_status = get_my_response_status(ee_clean, attendees_key,
                                           response_key)
        if my_status != 'accepted':
            continue
        ee_clean[response_key] = my_status
        del ee_clean[attendees_key]

        # Hide private events
        # Possible values:
        # - "default": Uses the default visibility for events on the calendar
        # - "public": Event is public and event details are visible to all readers of the calendar
        # - "private": Event is private and only event attendees may view event details
        # - "confidential": Event is private. This value is provided for compatibility reasons.
        if ee_clean.get('visibility', '') == 'private':  # TODO: fix when default visibility is private
            ee_clean = dict(ee_clean, **{'summary': DEFAULT_STATUS})

        events_clean.append(ee_clean)

    return events_clean


def strptime(str_time: str) -> datetime:
    fmt: str = '%Y-%m-%dT%H:%M:%S'
    set_utc: bool = False
    if len(str_time) >= 20 and str_time[19] == '.':
        fmt += '.%f'
    if str_time[-1] == 'Z':
        fmt += 'Z'
        set_utc = True
    elif re.match(r'[\+\-]\d{2}:\d{2}', str_time[-6::]):
        str_time = str_time[:-6:] + str_time[-6::].replace(':', '')
        fmt += '%z'
    dt = datetime.strptime(str_time, fmt)
    if set_utc:
        return dt.replace(tzinfo=pytz.UTC)
        # alternative: pytz.utc.localize(dt)
    else:
        return dt


def is_within_next_event(now: datetime, next_event: dict,
                         minutes_before: float = MINUTES_BEFORE) -> bool:
    """
    Check if `now` is during, or `minutes_before` minutes before the
    `next_event`.

    Parameters
    ----------
    now : datetime
        Instant representing now.
    next_event : dict
        Next event dictionary, at least with keys `start` and `end`.
    minutes_before : float
        Minutes before the event, if `now` is before the start of the event.

    Returns
    -------
    bool
    """
    assert minutes_before >= 0, "`minutes_before` must be a non-negative number."
    if next_event:
        start_dt = strptime(next_event['start'])
        end_dt = strptime(next_event['end'])
        logging.debug(f'Event starts at = {start_dt}')
        logging.debug(f'Event finishes at = {end_dt}')
        logging.debug(f'Now = {now}')
        if ((start_dt - now).total_seconds() <= minutes_before*60 or \
            start_dt <= now) and (now <= end_dt):
            return True
    return False


# Slack functions
# =============================================================================

from slackclient import SlackClient


def set_status(sc, text: str, emoji: str = ':spiral_calendar_pad:',
               expiration_unixtimestamp: int = 0) -> bool:
    resp = sc.api_call('users.profile.set',
                       profile={'status_text': text,
                                'status_emoji': emoji,
                                'status_expiration': expiration_unixtimestamp})
    return resp['ok']


# Google + Slack functions
# =============================================================================

def set_status_if_within_range(now: str):

    service = get_calendar()

    next_events = clean_events(get_next_events(now, 1, service))
    logging.debug(f'Next events (clean) = {next_events}')
    
    next_event = next_events[0] if next_events else {}
    
    if is_within_next_event(strptime(now), next_event):
        expiration = int(strptime(next_event['end']).timestamp()) or 0
        text = f"Meeting: {next_event['summary'] or DEFAULT_STATUS}"
        sc = SlackClient(token=os.environ['SLACK_TOKEN'])
        ok = True
        ok = set_status(sc,
                        text=text,
                        expiration_unixtimestamp=expiration)
        if ok:
            logging.info(f'Status was set to "{text:s}" until {datetime.fromtimestamp(expiration)}.')
        else:
            logging.info('Something went wrong.')
    else:
        logging.info('Still not close to the next event.')


# Run script
# =============================================================================

if __name__ == '__main__':

    now = datetime.utcnow().isoformat() + 'Z'  # 'Z' --> UTC
    # now = '2019-02-21T10:30:00-03:00'  # to debug
    logging.info('--------------------------------------------------------------------------------')
    logging.info(f'Now = {strptime(now)}')
    set_status_if_within_range(now)
