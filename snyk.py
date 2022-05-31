import sys
import os
import json
import subprocess
import logging
import shutil
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

BOLD = '\033[1m'
UNBOLD = '\033[0;0m'

MONITOR_SUCCESS = None
TEST_SUCCESS = None
EVENTS = {
    'fail': 'snyk.scan.fail',
    'pass': 'snyk.scan.pass',
    'error': 'snyk.scan.error'
}

# extract out environment variables for safe usage
try:
    # mandatory fields
    REPOSITORY = os.environ['REPOSITORY']
    REPOSITORY_SLUG = os.environ['REPOSITORY_SLUG']
    LANGUAGE =  os.environ['LANGUAGE'] if 'LANGUAGE' in os.environ else ''
    ALL_SUBPROJECTS =  True if 'ALLSUBPROJECTS' in os.environ and 'true' in os.environ['ALLSUBPROJECTS'] else False
    # METRICS_TOPIC_ARN = os.environ['METRICS_TOPIC_ARN']
    ORG = os.environ['ORG']
    PACKAGE_MANAGER = os.environ['PACKAGE_MANAGER'] if 'PACKAGE_MANAGER' in os.environ else ''
    BLOCK = False if 'BLOCK' in os.environ and 'false' in os.environ['BLOCK'] else True
    PATH = os.environ['DEPENDENCY_PATH'] if 'DEPENDENCY_PATH' in os.environ else ''
    SEVERITY = os.environ['SEVERITY'] if 'SEVERITY' in os.environ else ''
    SCAN_DEV_DEPS = 'SCAN_DEV_DEPS' in os.environ and 'true' == os.environ['SCAN_DEV_DEPS']

except Exception as e:
    logger.error('failed to extract environment variables')
    logger.exception(e)
    exit(0)

SEVERITY_MAPPING = {
    'low': 0,
    'medium': 1,
    'high': 2,
    'critical': 3,
}

def check_for_snyk_test_error(result):
    if 'error' in result:
        TEST_SUCCESS = False
        raise Exception('snyk test returned an error: {}'.format(result['error']))

def snyk_test():
    EXIT_CODE = 0
    os.chdir(REPOSITORY)
    command = ['snyk', 'test', '--json', '--org={}'.format(ORG), '--project-name={}'.format(REPOSITORY_SLUG)]
    if PATH:
        print('Explicit path specified')
        command.append('--file={}'.format(PATH))
    if SCAN_DEV_DEPS:
        command.append('--dev')
    if PACKAGE_MANAGER:
        command.append(f'--packageManager={PACKAGE_MANAGER}')
    if ALL_SUBPROJECTS:
        command.append('--all-sub-projects')

    response = subprocess.run(command, stdout=subprocess.PIPE)
    results = json.loads(response.stdout.decode())

    global TEST_SUCCESS
    TEST_SUCCESS = True
    vulns = []
    if ALL_SUBPROJECTS:
        for single_result in results:
            check_for_snyk_test_error(single_result)
            if len(single_result['vulnerabilities']) > 0:
                for v in single_result['vulnerabilities']:
                    vulns.append(v)
    else:
        check_for_snyk_test_error(results)
        vulns = results['vulnerabilities']


    results_seen = {
        'low': {},
        'medium': {},
        'high': {},
        'critical': {},
    }
    for result in vulns:
        # skip over license results for the time being
        if 'license' in result:
            continue

        introduced_from = result['from']
        severity = result['severity']
        if result['id'] in results_seen[severity]:
            results_seen[severity][result['id']]['from'].append(result['from'])
            results_seen[severity][result['id']]['upgradePath'].append(result['upgradePath'])
        else:
            results_seen[severity][result['id']] = {
                'moduleName': result['moduleName'],
                'title': result['title'],
                'severity': result['severity'],
                'isUpgradable': result['isUpgradable'],
                'isPatchable': result['isPatchable'],
                'from': [introduced_from],
                'upgradePath': [result['upgradePath']]
            }

    vulnerable_paths = 0
    for severity in results_seen:
        for key in results_seen[severity]:
            result = results_seen[severity][key]
            introduced_value = []
            for dependency_tree in result['from']:
                introduced_value.append(dependency_tree[1])
            message = '{}{} severity found in {}{}\n'.format(BOLD, result['severity'].capitalize(), result['moduleName'], UNBOLD)
            message += ' -> Description: {}\n'.format(result['title'])
            message += ' -> Info: https://snyk.io/vuln/{}\n'.format(key)
            message += ' -> Introduced through: {}\n'.format(', '.join(introduced_value[:5]))

            vulnerable_paths += len(result['from'])

            for from_list in result['from'][:3]:
                message += ' -> From: {}\n'.format(' > '.join(from_list[:3]))

            if len(result['from']) > 3:
                message += ' -> and {} more...\n'.format(len(result['from']) - 3)

            if result['isUpgradable']:
                message += BOLD + 'Remediation: \n\t Upgrade {} to {} (triggers upgrades to {})\n'.format(result['from'][0][1], result['upgradePath'][0][1], ' > '.join(result['upgradePath'][0][1:])) + UNBOLD
            print(message)

    if not ALL_SUBPROJECTS:
        summary = 'Tested {} dependencies for known issues, found {} issues, {} vulnerable paths\n'.format(results['dependencyCount'], results['uniqueCount'], vulnerable_paths)
        print(summary)

    for severity in results_seen:
        if SEVERITY_MAPPING[SEVERITY] <= SEVERITY_MAPPING[severity] and len(results_seen[severity]) > 0:
            EXIT_CODE = 1
    return EXIT_CODE

def check_monitor_result(result):
    success = False if 'error' in result else True
    if not success:
        MONITOR_SUCCESS = False
        raise Exception('snyk monitor returned an error')

    message = 'Taking snapshot of project dependencies!\n'
    message += 'Vulnerabilities for the project can be found here: {}, where vulnerabilites can be ignored for subsequent scans.'.format(result['uri'].rsplit('/history')[0])

    print(message)

def snyk_monitor():
    command = ['snyk', 'monitor', '--json', '--org={}'.format(ORG)]

    # monitor doesnt support all-sub-projects and project-name in the same command line.
    if ALL_SUBPROJECTS:
        command.append('--all-sub-projects')
    else:
        command.append('--project-name={}'.format(REPOSITORY_SLUG))
    if PATH:
        command.append('--file={}'.format(PATH))
    if SCAN_DEV_DEPS:
        command.append('--dev')
    if PACKAGE_MANAGER:
        command.append(f'--packageManager={PACKAGE_MANAGER}')


    response = subprocess.run(command, stdout=subprocess.PIPE)
    results = json.loads(response.stdout.decode())

    global MONITOR_SUCCESS
    MONITOR_SUCCESS = True

    if ALL_SUBPROJECTS:
        for single_result in results:
            check_monitor_result(single_result)
    else:
        check_monitor_result(results)


if __name__ == "__main__":
    EXIT_CODE = None
    try:
        subprocess.run(['snyk', 'auth', os.environ['SNYK_TOKEN']])
    except Exception as e:
        logger.error('config error')
        logger.exception(e)
        exit(0)

    for attempt in range(0,3):
        try:
            EXIT_CODE = snyk_test()
            snyk_monitor()
        except Exception as e:
            logger.error('error running test and monitor')
            logger.exception(e)
            EXIT_CODE = None
            continue
        break

    if EXIT_CODE == None:
        exit(0)
    elif EXIT_CODE == 0:
        exit(0)
    elif EXIT_CODE == 1:
        if BLOCK:
            exit(1)
        exit(0)
