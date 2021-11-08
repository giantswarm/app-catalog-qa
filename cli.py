from pprint import pprint
import click
from datetime import datetime
from os import getenv
import re
from typing import AsyncContextManager, Optional, Tuple

from colored import fg, attr
from dateutil.parser import isoparse
from dateutil.relativedelta import *
import github
from github.GithubException import UnknownObjectException
import requests
import semver
import yaml
import pytz

utc=pytz.UTC

# GitHub organisation expected to own the app home repository
GITHUB_REPO_ORG = 'giantswarm'

# Some chart annotation keys we'll look for
ANNOTATIONS_TEAM          = 'application.giantswarm.io/team'
ANNOTATIONS_README        = 'application.giantswarm.io/readme'
ANNOTATIONS_METADATA      = 'application.giantswarm.io/metadata'
ANNOTATIONS_VALUES_SCHEMA = 'application.giantswarm.io/values-schema'

# These globals are used in our functions
GITHUB_CLIENT = None
CONF = None
KEYWORD_RE = None
CODEOWNER_TEAM_RE = None

@click.command()
@click.option('--conf', default='./config.yaml', help='Configuration file path.')
@click.option('--token-path', default='~/.github-token', help='Github token path.')
@click.option('--app-name', 'app_filter', help='Only report for this app', multiple=True)
def main(conf, token_path, app_filter):
    global GITHUB_CLIENT
    global CONF
    global KEYWORD_RE
    global CODEOWNER_TEAM_RE

    CONF = read_config(conf)

    KEYWORD_RE = re.compile(CONF['keyword_pattern'])
    CODEOWNER_TEAM_RE = re.compile(CONF['codeowner_team_pattern'])

    if token_path is None:
        GITHUB_CLIENT = github.Github()
    else:
        token = read_token(token_path)
        GITHUB_CLIENT = github.Github(token)

    for cat in CONF['catalogs']:
        error_count = 0
        warning_count = 0
        suggestions_count = 0
        accolades_count = 0
        index = load_catalog_index(cat['url'])
        
        if app_filter == ():
            print(f'\n## Catalog `{cat["name"]}` - {len(index["entries"])} apps')

        for app_name in index['entries'].keys():

            # Apply app filter
            if app_filter != ():
                if app_name not in app_filter:
                    continue

            result = validate_app_releases(index['entries'][app_name])
            error_count += len(result['errors'])
            warning_count += len(result['warnings'])
            suggestions_count  += len(result['suggestions'])
            accolades_count += len(result['accolades'])

            if len(result['errors']) + len(result['warnings']) > 0:
                errinfo = ''
                if len(result['errors']) > 0:
                    errinfo += f"{len(result['errors'])} errors and "
                errinfo += f"{len(result['warnings'])} warnings"

                app_label = f'`{app_name}`'
                if result['repo_url'] is not None:
                    app_label = f"[{app_name}]({result['repo_url']})"
                
                app_owner = '_no owner_'
                if result['owner'] is not None:
                    app_owner = result['owner']

                print(f'\n### {app_label} ({app_owner}) -- {errinfo}')

            print('\n<details>')

            print(f'\nInformation based on release v{result["latest_release"]}')

            if len(result['errors']):
                print(f"\n#### {attr('bold')}{fg('red')}Errors{attr('reset')}\n")
                for error in result['errors']:
                    print(f"- [ ] {fg('red')}{error}{attr('reset')}")

            if len(result['warnings']):
                print(f"\n#### {attr('bold')}{fg('yellow')}Warnings{attr('reset')}\n")
                for warning in result['warnings']:
                    print(f"- [ ] {fg('yellow')}{warning}{attr('reset')}")

            if len(result['suggestions']):
                print(f"\n#### {attr('bold')}{fg('yellow')}Suggestions{attr('reset')}\n")
                for item in result['suggestions']:
                    print(f"- [ ] {item}")

            # if len(result['accolades']):
            #     print(f"\n#### {attr('bold')}{fg('yellow')}Looking good{attr('reset')}\n")
            #     for item in result['accolades']:
            #         print(f"- {fg('green')}{item}{attr('reset')}")

            print('\n</details>')

        print(f'\n{error_count} errors, {warning_count} warnings, {suggestions_count} suggestions, {accolades_count} accolades in total')


def validate_app_releases(releases: list) -> dict:
    """
    Validates whether a latest release can be found
    and if yes, does a deeper check on the latest release.
    """
    ret = {
        'errors': [],
        'warnings': [],
        'suggestions': [],
        'accolades': [],
        'repo_url': None,
        'owner': None,
        'latest_release': None,
    }
    
    releases_dict = {}   # dict with version string as key

    for release in releases:
        if release['version'] in releases_dict:
            ret['errors'].append(f'Duplicate release {release["version"]}')
            continue

        releases_dict[release['version']] = release

    try:
        ret['latest_release'] = latest_version(releases_dict.keys())
        result = validate_app_release(releases_dict[ret['latest_release']])
        ret['errors'] += result['errors']
        ret['warnings'] += result['warnings']
        ret['suggestions'] += result['suggestions']
        ret['accolades'] += result['accolades']
        ret['repo_url'] = result['repo_url']
        ret['owner'] = result['owner']
    except ValueError as e:
        ret['errors'].append(f'Could not validate latest version: {str(e)}')

    return ret


def validate_app_release(release: dict) -> dict:
    """
    Valide a specific release of an app and
    return reault dict.
    """
    global KEYWORD_RE

    ret = {
        'errors': [],
        'warnings': [],
        'suggestions': [],
        'accolades': [],
        'repo_url': None,
        'owner': None,
    }

    urls = []

    owner_annotation = None
    owner_codeowners = None

    # Giant Swarm owned repo for this app
    github_repo_handle = None

    # required fields
    for field in ('apiVersion', 'created', 'description', 'digest', 'name', 'version'):
        ret = check_condition(field in release, ret,
                              error=f'No `{field}` given')
    
    # apiVersions
    ret = check_condition(release['apiVersion'] in ('v1', 'v2'), ret,
                          error=f'Invalid helm chart apiVersion value `{release["apiVersion"]}`')
    ret = check_condition(release['apiVersion'] == 'v1', ret,
                          suggestion='Migrate helm chart to apiVersion v2')

    # recommended fields
    for field in ('appVersion', 'icon', 'sources', 'urls'):
        ret = check_condition(field in release, ret,
                              warning=f'No `{field}` given',
                              accolade=f'Chart specifies the `{field}` field')
    
    # suggested fields
    for field in ('keywords', 'kubeVersion', 'maintainers'):
        ret = check_condition(field in release, ret,
                              suggestion=f'Specify `{field}` attribute',
                              accolade=f'Chart specifies the `{field}` field')
    
    # additional fields
    ret = check_condition('dependencies' in release, ret,
                          suggestion='Use `dependencies` to inform about required apps/charts',
                          accolade='Chart specifies `dependencies`')
    
    # Deeper evaluations
    ret = check_condition(not release['name'].endswith('-app'), ret,
                          warning='App name should not end with `-app`')

    if 'description' in release:
        ret = check_condition('helm chart for' not in release['description'].lower(), ret,
                              warning=f'Description should be unique and meaningful (is: `{release["description"]}`)')
    
    # URLs
    ret = check_condition('home' in release, ret,
                          error=f'Field `home` not set, must be set to a `https://github.com/{GITHUB_REPO_ORG}/...` repository URL')

    if 'home' in release:
        urls.append(release['home'])

        ret = check_condition(release['home'].startswith(f'https://github.com/{GITHUB_REPO_ORG}/'), ret,
                              warning=f'URL in `home` should point to a GitHub repo owned by {GITHUB_REPO_ORG} (is {release["home"]})',
                              accolade=f'URL in `home` points to a GitHub repo owned by {GITHUB_REPO_ORG}')

        if release['home'].startswith(f'https://github.com/{GITHUB_REPO_ORG}/'):
            segments = release['home'].split('/')
            github_repo_handle = f'{GITHUB_REPO_ORG}/{segments[4]}'
            ret['repo_url'] = release['home']
        
        valid, status_code = check_url(release['home'])
        ret = check_condition(valid, ret,
                              error=f'URL in `home` is invalid, status code {status_code} - `{release["home"]}`')
    
    if 'icon' in release:
        ret = check_condition(release['icon'].startswith('https://s.giantswarm.io/app-icons/'), ret,
            warning=f'Icon URL should start with `https://s.giantswarm.io/app-icons/` (is {release["icon"]})',
            accolade='Icon is hosted on our server s.giantswarm.io')
        
        valid, status_code = check_url(release['icon'])

        ret = check_condition(valid, ret, error=f'Icon URL is invalid, status code {status_code} - `{release["icon"]}`')
        
        ret = check_condition(release['icon'].lower().endswith('.svg'), ret,
            warning=f'Icon should use the SVG format - currently: `{release["icon"]}`',
            accolade='Icon is in the SVG format')
    
    if 'keywords' in release:
        ret = check_condition(len(release['keywords']) > 0, ret, error=f'Keywords list is empty')
        
        if len(release['keywords']) > 0:
            for kw in release['keywords']:
                ret = check_condition(KEYWORD_RE.match(kw) is not None, ret, warning=f'Keyword doesn\'t match the expected format: `{kw}`')
    
    if 'type' in release:
        ret = check_condition(release['type'] == 'application', ret,
                              error=f"Chart field `type` should be `application` but is `{release['type']}` instead")

    if 'annotations' in release:
        for annotation in (ANNOTATIONS_METADATA,
                           ANNOTATIONS_README,
                           ANNOTATIONS_VALUES_SCHEMA):
            
            ret = check_condition(annotation in release['annotations'], ret,
                                  warning=f'Annotation `{annotation}` should be set')

            if annotation in release['annotations']:
                url = release['annotations'][annotation]
                valid, status_code = check_url(release['annotations'][annotation])

                ret = check_condition(valid, ret, error=f'URL in annotation `{annotation}` is invalid, status code {status_code} - `{release["home"]}`')
                if valid:
                    # Deeper README analysis
                    if annotation == ANNOTATIONS_README:
                        if 'version' in release:

                            ret = check_condition(release['version'] in release['annotations'][annotation], ret,
                                warning=f"README URL {release['annotations'][annotation]} does not appear to be versioned",
                                accolade=f'README URL appears to be versioned')

                        err, warn, acc = validate_readme(release['annotations'][annotation])
                        ret['errors'] += err
                        ret['warnings'] += warn
                        ret['accolades'] += acc

        ret = check_condition(ANNOTATIONS_TEAM in release['annotations'], ret,
            warning=f'Annotation `{ANNOTATIONS_TEAM}` should be set',
            accolade=f'Team ownership is exposed via annotation')

        if ANNOTATIONS_TEAM in release['annotations']:
            owner_annotation = release['annotations'][ANNOTATIONS_TEAM]

            ret = check_condition('-' in release['annotations'][ANNOTATIONS_TEAM], ret,
                warning=f"Owner name in team annotation `{release['annotations'][ANNOTATIONS_TEAM]}` does not look like a proper GitHub team name, misses prefix like `team-`")

    if 'created' in release:
        created = isoparse(release['created']).replace(tzinfo=utc)
        now = datetime.utcnow().replace(tzinfo=utc)
        age = now - created
        days = age.total_seconds() / 60 / 60 / 24

        ret = check_condition(days <= 100, ret,
                              warning=f'Latest release is older than 100 days',
                              accolade=f'Latest release is fresh ({int(days)} days old)')

    if 'deprecated' in release:
        if release['deprecated'] == True:
            ret['warnings'].append(f'Latest release is marked as deprecated')

    for field in ('sources', 'urls'):
        if field in release:
            for url in release[field]:
                urls.append(url)

                valid, status_code = check_url(url)
                ret = check_condition(valid, ret,
                    error=f'URL in `{field}` is invalid, status code {status_code} - `{url}`')
    
    if 'maintainers' in release:
        for item in release['maintainers']:
            if 'url' in item:
                urls.append(item['url'])

                valid, status_code = check_url(item['url'])
                ret = check_condition(valid, ret,
                    error=f'URL in maintainer is invalid, status code {status_code} - `{item["url"]}`')

    # Look for duplicate URLs
    dupe_urls = get_duplicates(urls)
    if len(dupe_urls) > 0:
        for url in urls:
            ret = check_condition(url not in dupe_urls, ret,
                warning=f'URL is used in more than one field: `{url}`')
    
    # Look into app GitHub repo
    if github_repo_handle is None:
        ret['errors'].append('Could not detect GitHub repo for this app')
    else:
        repo_exists = github_repo_exists(github_repo_handle)
        if repo_exists:
            codeowners = get_github_repo_file(github_repo_handle, 'CODEOWNERS')
            if codeowners is None:
                ret['warnings'].append(f'Repo {github_repo_handle} should have a `CODEOWNERS` file')
            else:
                ret['accolades'].append(f'Repo {github_repo_handle} has a `CODEOWNERS` file')
                matches = CODEOWNER_TEAM_RE.findall(codeowners.decode('utf-8'))
                if matches is None:
                    ret['warnings'].append(f'CODEOWNERS file does not seem to contain any team name')
                else:
                    if len(matches) == 1:
                        owner_codeowners = matches[0]
                    else:
                        owner_codeowners = matches
            
            # Check more files
            for path in ('README.md', 'LICENSE', 'SECURITY.md', 'DCO', 'CONTRIBUTING.md'):
                content = get_github_repo_file(github_repo_handle, path)
                if content is None:
                    ret['warnings'].append(f'Repo {github_repo_handle} should have a `{path}` file')
                else:
                    ret['accolades'].append(f'Repo {github_repo_handle} has a `{path}` file')

    # Owners
    owner = set()
    if owner_annotation is not None:
        owner.add(owner_annotation)
    if owner_codeowners is not None:
        if type(owner_codeowners) == str:
            owner.add(owner_codeowners)
        elif type(owner_codeowners) == list:
            for o in owner_codeowners:
                owner.add(o)
    
    if len(owner) == 1:
        ret['accolades'].append(f'App data exposes a single owner `{list(owner)[0]}`')
        ret['owner'] = list(owner)[0]
    elif len(owner) > 1:
        ret['errors'].append(f'App data exposes various owners `{" ".join(list(owner))}`')
    else:
        ret['errors'].append(f'App does not have a visible owner')

    return ret


def check_condition(expression, results, error=None, warning=None, suggestion=None, accolade=None):
    if expression == True:
        if accolade is not None:
            results['accolades'].append(accolade)
    else:
        if error is not None:
            results['errors'].append(error)
        elif warning is not None:
            results['warnings'].append(warning)
        elif suggestion is not None:
            results['suggestions'].append(suggestion)

    return results


def latest_version(version_strings):
    """
    Returns the latest semver from a list of version strings
    """
    versions_tuples = [] # list of tuples (semver, string)

    for vstring in version_strings:
        is_valid = semver.VersionInfo.isvalid(vstring)
        if not is_valid:
            raise ValueError(f"Version string not semver conformant: '{vstring}'")
        
        semver_version = semver.VersionInfo.parse(vstring)
        versions_tuples.append((semver_version, vstring))

    versions_sorted = sorted(versions_tuples, key=cmp_to_key(semver_cmp))

    return versions_sorted[-1][1]


def semver_cmp(v1, v2):
    return v1[0].compare(v2[0])


def cmp_to_key(mycmp):
    'Convert a cmp= function into a key= function'
    class K(object):
        def __init__(self, obj, *args):
            self.obj = obj
        def __lt__(self, other):
            return mycmp(self.obj, other.obj) < 0
        def __gt__(self, other):
            return mycmp(self.obj, other.obj) > 0
        def __eq__(self, other):
            return mycmp(self.obj, other.obj) == 0
        def __le__(self, other):
            return mycmp(self.obj, other.obj) <= 0  
        def __ge__(self, other):
            return mycmp(self.obj, other.obj) >= 0
        def __ne__(self, other):
            return mycmp(self.obj, other.obj) != 0
    return K


def get_duplicates(thelist: list) -> list:
    thelist = sorted(thelist)
    items = set()
    dupes = set()
    for i in thelist:
        if i in items:
            dupes.add(i)
        else:
            items.add(i)
    return list(dupes)


def load_catalog_index(url: str) -> dict:
    r = requests.get(url)
    data = yaml.load(r.text, Loader=yaml.Loader)
    return data


def read_config(path: str) -> dict:
    with open(path, "r") as input:
        data = yaml.load(input, Loader=yaml.Loader)
        return data


def check_url(url: str) -> Tuple[bool, int]:
    """
    Load URL and return tuple (valid, status_code)
    """
    try:
        r = requests.head(url, timeout=10, headers={
            'user-agent': CONF['user_agent'],
        })
        return str(r.status_code)[0] == '2', r.status_code
    except Exception as e:
        return False, 0


def validate_readme(url: str) -> Tuple[list, list, list]:
    """
    Obtain and validate the README from the given URL
    and return errors, warnings, accolades
    """
    errors = []
    warnings = []
    accolades = []

    r = requests.get(url, timeout=10)
    if r.status_code >= 400:
        errors.append(f'Error fetching README URL {url}: status {r.status_code}')
    else:
        content = r.text

        # length
        if len(content) < 500:
            errors.append(f'README content too short')
        elif len(content) < 1000:
            warnings.append(f'README content could be longer')
        else:
            accolades.append(f'README content appears reasonably long ({len(content)} chars)')
        
        # placeholder
        if '{APP-NAME}' in content:
            errors.append('README contains placeholder `{APP-NAME}`')

    return errors, warnings, accolades


def github_repo_exists(repo_handle: str) -> bool:
    global GITHUB_CLIENT
    try:
        _ = GITHUB_CLIENT.get_repo(repo_handle)
        return True
    except UnknownObjectException:
        return False


def get_github_repo_file(repo_handle: str, path: str) -> Optional[bytes]:
    global GITHUB_CLIENT
    try:
        repo = GITHUB_CLIENT.get_repo(repo_handle)
        file = repo.get_contents(path=path)
        return file.decoded_content
    except UnknownObjectException:
        return None


def read_token(path: str) -> str:
    path = path.replace('~', getenv('HOME'))
    with open(path, "r") as input:
        token = input.readline()
        return token.strip()


if __name__ == '__main__':
    main()
