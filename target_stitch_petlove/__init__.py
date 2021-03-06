#!/usr/bin/env python3

import argparse
import io
import sys
import json
import threading
import http.client
import urllib
import collections
import time

import pkg_resources
from jsonschema.validators import Draft4Validator
import singer

logger = singer.get_logger()

def emit_state(state):
    if state is not None:
        line = json.dumps(state)
        logger.debug('Emitting state {}'.format(line))
        sys.stdout.write("{}\n".format(line))
        sys.stdout.flush()

def flatten(d, parent_key='', sep='__'):
    items = []
    for k, v in d.items():
        new_key = parent_key + sep + k if parent_key else k
        if isinstance(v, collections.MutableMapping):
            items.extend(flatten(v, new_key, sep=sep).items())
        else:
            items.append((new_key, str(v) if type(v) is list else v))
    return dict(items)
        
def persist_lines(config, lines):
    state = None
    schemas = {}
    key_properties = {}
    validators = {}
    data = {}
    max_batch_size = config.get('batch_size', 1000)
    batch_size = 0
    number_of_the_current_batch = 1
    messages = []
    timestamp = int(time.time())
    # Loop over lines from stdin
    for line in lines:
        try:
            o = json.loads(line)
        except json.decoder.JSONDecodeError:
            logger.error("Unable to parse:\n{}".format(line))
            raise

        if 'type' not in o:
            raise Exception("Line is missing required key 'type': {}".format(line))
        t = o['type']

        if t == 'RECORD':
            if 'stream' not in o:
                raise Exception("Line is missing required key 'stream': {}".format(line))
            if o['stream'] not in schemas:
                raise Exception("A record for stream {} was encountered before a corresponding schema".format(o['stream']))

            # Get schema for this record's stream
            schema = schemas[o['stream']]

            # Validate record
            validators[o['stream']].validate(o['record'])

            # If the record needs to be flattened, uncomment this line
            # flattened_record = flatten(o['record'])

            batch_size = batch_size + 1

            record = {'action': 'upsert', 'data': o['record'], 'sequence': (timestamp + batch_size)}
            messages.append(record)

            # TODO: Process Record message here..

            state = None
        elif t == 'STATE':
            logger.debug('Setting state to {}'.format(o['value']))
            state = o['value']
        elif t == 'SCHEMA':
            if 'stream' not in o:
                raise Exception("Line is missing required key 'stream': {}".format(line))
            stream = o['stream']
            schemas[stream] = o['schema']
            validators[stream] = Draft4Validator(o['schema'])
            if 'key_properties' not in o:
                raise Exception("key_properties field is required")
            key_properties[stream] = o['key_properties']
        else:
            raise Exception("Unknown message type {} in message {}"
                            .format(o['type'], o))

        if batch_size > max_batch_size:
            print("SENDING BATCH: " + str(number_of_the_current_batch))
            data = {'schema': schema, 'table_name': config.get("table_name", ""), 'messages': messages}
            post_data(config, data)
            messages = []
            timestamp = int(time.time())
            number_of_the_current_batch = number_of_the_current_batch + 1
            batch_size = 0

    if len(messages) > 0:
        data = {'schema': schema, 'table_name': config.get("table_name", ""), 'messages': messages}
        post_data(config, data)

    return state


def post_data(config, data):
    connection = http.client.HTTPSConnection(config.get("region_url", ""))
    headers = {'Content-type': 'application/json', 'Authorization': 'Bearer ' + config.get("token", "")}
    json_data = json.dumps(data)
    connection.request('POST', config.get("batch_api_path", ""), json_data, headers)
    response = connection.getresponse()
    print(response.read().decode())

def send_usage_stats():
    try:
        version = pkg_resources.get_distribution('target-csv').version
        conn = http.client.HTTPConnection('collector.singer.io', timeout=10)
        conn.connect()
        params = {
            'e': 'se',
            'aid': 'singer',
            'se_ca': 'target_stitch_petlove',
            'se_ac': 'open',
            'se_la': version,
        }
        conn.request('GET', '/i?' + urllib.parse.urlencode(params))
        response = conn.getresponse()
        conn.close()
    except:
        logger.debug('Collection request failed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', '--config', help='Config file')
    parser.add_argument('-t', '--token', help='API Token')
    parser.add_argument('-ta', '--table', help='API Table')
    args = parser.parse_args()

    if args.config:
        with open(args.config) as input:
            config = json.load(input)
    else:
        config = {
            "region_url": "api.stitchdata.com",
            "batch_api_path": "/v2/import/batch",
            "table_name": args.table,
            "batch_size": 500,
            "token": args.token
        }

    if not config.get('disable_collection', True):
        logger.info('Sending version information to singer.io. ' +
                    'To disable sending anonymous usage data, set ' +
                    'the config parameter "disable_collection" to true')
        threading.Thread(target=send_usage_stats).start()

    input = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
    state = persist_lines(config, input)
        
    emit_state(state)
    logger.debug("Exiting normally")


if __name__ == '__main__':
    main()
