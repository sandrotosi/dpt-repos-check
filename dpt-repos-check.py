import datetime
import logging
import os
import sys
from collections import defaultdict
from datetime import timedelta

import gitlab
import requests
import requests_cache
from debian.changelog import Changelog
from debian.deb822 import Deb822
from debian.debian_support import Version
from debian.watch import WatchFile

__version__ = '0.2.0'


class Violations(object):
    """
    Maintain the list of violations in a centralize place
    """

    def __init__(self):
        self.per_repo = defaultdict(list)
        self.per_violation = defaultdict(list)

    def add(self, repo, violation, extra_data=''):
        # this is a trick to just write `violations` if no `extra_data` is present
        # and if it's populated, then separate it from `violation` with a semicolon
        self.per_repo[repo].append(f'{violation}{"; " if extra_data else ""}{extra_data}')
        self.per_violation[violation].append(repo)

    def print(self):
        print("Stats:")
        print(f"    Repos with violations: {len(self.per_repo.keys())}")
        print(f"    Violations types detected: {len(self.per_violation.keys())}")
        print(f'    Total violations: {sum(len(x) for x in self.per_repo.values())}')
        print('\nPer repository violations:')
        for _repo, _violations in self.per_repo.items():
            print(_repo)
            for _violation in _violations:
                print(f"    {_violation}")

        print('\nPer violation repositories:')
        for _violation, _repos in self.per_violation.items():
            print(_violation)
            for _repo in _repos:
                print(f"    {_repo}")


def get_sid_version(srcpkg):
    # get the current version in Sid, from madison
    # example output:
    #
    # https://qa.debian.org/madison.php?package=matplotlib&text=on&s=sid&a=source,all,amd64
    # matplotlib | 3.5.0-1 | sid | source
    madison = requests.get(f"https://qa.debian.org/madison.php?package={srcpkg}&text=on&s=sid&a=source,all,amd64")
    if not madison.text:
        return None

    try:
        # rough but gets the job done
        return Version(madison.text.splitlines()[0].split(' | ')[1].strip())
    except Exception as e:
        logging.exception(e)
        return None


logging.basicConfig(format='%(asctime)s %(message)s', stream=sys.stdout, level=logging.DEBUG)

# TODO: remove, this is only for development
requests_cache.install_cache(
    'dpt_repos_check_cache',
    cache_control=False,
    expire_after=timedelta(days=15),
    backend='filesystem',
    serializer='json',
)

SALSA_TOKEN = os.environ.get('SALSA_TOKEN', None)

# 9360 is the group_id for python-team/packages subgroup, it could be automatically obtained
# from https://salsa.debian.org/api/v4/groups/python-team/subgroups/ but meh
GROUPID = 9360

logging.info("Gather DPT projects from Salsa")
salsa = gitlab.Gitlab('https://salsa.debian.org/', private_token=SALSA_TOKEN)
group = salsa.groups.get(GROUPID)
group_projects = group.projects.list(all=True, order_by='name', sort='asc', as_list=True)

violations = Violations()

# TODO: pristine-tar: contains .delta for latest upload to archive
# TODO: pristine-tar: onbtain the tarball and compare with the archive
# TODO: check for packages no longer in debian but with repo still in the team
# TODO: check for packages referring the team in maint/upl but with no repo in the team

for group_project in group_projects:
    project = salsa.projects.get(group_project.id)
    logging.info(f"CHECKING {project.name}...")

    # Branches checks

    branches = {x.name for x in project.branches.list()}

    if not branches:
        violations.add(project.name, 'ERROR: appears to be an empty repository')
        continue

    # DEP-14 is the recommendation doc for git layout: https://dep-team.pages.debian.net/deps/dep14/
    if not branches.intersection({'master', 'debian/master', 'debian/unstable', 'debian/latest'}):
        if branches.intersection({'sid', 'debian/sid'}):
            violations.add(project.name, f'WARNING: uncommon debian master branch (DEP-14)', extra_data=f'available branches={branches}')
        else:
            violations.add(project.name, f'ERROR: no valid Debian master branch', extra_data=f'available branches={branches}')

    if not branches.intersection({'upstream', 'upstream/latest'}):
        violations.add(project.name, f'ERROR: no upstream branch', extra_data=f'available branches={branches}')

    if 'pristine-tar' not in branches:
        violations.add(project.name, f'ERROR: no pristine-tar branch', extra_data=f'available branches={branches}')

    # debian/ exists check

    debian_directory_exists = any([x['name'] == 'debian' for x in project.repository_tree()])
    if not debian_directory_exists:
        violations.add(project.name, f'ERROR: theres no debian/ directory in the default branch, which should contain a development branch, see DEP-14; all other checks are skipped',
                       extra_data=f'default branch={project.default_branch}')
        continue

    # debian/control checks

    d_control_id = [d['id'] for d in project.repository_tree(path='debian', all=True) if d['name'] == 'control'][0]
    d_control = Deb822(project.repository_raw_blob(d_control_id))

    if project.name != d_control["Source"]:
        violations.add(project.name, f'ERROR: repo name does not match the package source name', extra_data=f'repo name={project.name}, src name={d_control["Source"]}')

    if 'Uploaders' not in d_control:
        violations.add(project.name, 'WARNING: Uploaders is missing from debian/control, that doesnt seem right')

    maints = d_control['Maintainer']+d_control.get('Uploaders', '')
    if all(
        x not in maints
        for x in (
            'team+python@tracker.debian.org',
            'python-apps-team@lists.alioth.debian.org',
            'python-modules-team@lists.alioth.debian.org',
        )
    ):
        violations.add(project.name, 'ERROR: DPT is not in Maintainer or Uploaders fields')
    elif 'team+python@tracker.debian.org' not in maints:
        violations.add(project.name, 'WARNING: still using the old team email address')

    if not (vcs_browser := d_control.get('Vcs-Browser')):
        violations.add(project.name, f'ERROR: Vcs-Browser field is missing from debian/control')
    elif vcs_browser != project.web_url:
        violations.add(project.name, f'ERROR: Vcs-Browser field doesnt match the repo web URL', extra_data=f'Vcs-Browser={vcs_browser}, repo web URL={project.web_url}')
    if not (vcs_git := d_control.get('Vcs-Git')):
        violations.add(project.name, f'ERROR: Vcs-Git field is missing from debian/control')
    elif vcs_git != project.http_url_to_repo:
        violations.add(project.name, f'ERROR: Vcs-Git field doesnt match the repo git URL', extra_data=f'Vcs-Git={vcs_git}, repo git URL={project.http_url_to_repo}')

    # debian/watch checks

    d_watch_id = [d['id'] for d in project.repository_tree(path='debian', all=True) if d['name'] == 'watch']
    if d_watch_id:
        d_watch = project.repository_raw_blob(d_watch_id[0]).decode().lower()

        try:
            watchfile = WatchFile.from_lines(d_watch.splitlines())

            for w_entry in watchfile.entries:
                if 'pypi.python.org' in w_entry.url or 'pypi.debian.net' in w_entry.url:
                    violations.add(project.name, 'WARNING: debian/watch still uses PyPI to track new releases, https://lists.debian.org/debian-python/2021/06/msg00026.html')
        except:
            violations.add(project.name, 'ERROR: unable to parse debian/watch')
    else:
        violations.add(project.name, 'ERROR: debian/watch is missing')

    # sid version check

    sid_version = get_sid_version(d_control["Source"])
    if not sid_version:
        violations.add(project.name, 'WARNING: unable to find a version in Sid: is this still in NEW/experimental-only?')
    else:
        # tags checks

        tags = [x.name for x in project.tags.list()]

        if (debian_tag := f'debian/{sid_version.full_version}') not in tags:
            violations.add(project.name, f"ERROR: there's no debian tag in the repo corresponding to the sid version", extra_data=f'sid version={sid_version}, missing tag={debian_tag}')

        if (upstream_tag := f'upstream/{sid_version.upstream_version}') not in tags:
            violations.add(project.name, f"ERROR: there's no upstream tag in the repo corresponding to the sid version", extra_data=f'sid version={sid_version}, missing tag={upstream_tag}')

        # debian/changelog checks

        d_changelog_id = [d['id'] for d in project.repository_tree(path='debian', all=True) if d['name'] == 'changelog'][0]
        d_changelog = Changelog(project.repository_raw_blob(d_changelog_id))

        if not any(x.version == sid_version for x in d_changelog._blocks):
            violations.add(project.name, f"ERROR: debian/changelog doesnt contain an entry for the version in sid", extra_data=f"sid version={sid_version}")

    # webhooks checks

    if SALSA_TOKEN:
        hooks = project.hooks.list()
        if not any('tagpending' in x.url for x in hooks):
            violations.add(project.name, f"WARNING: tagpending webhook missing")
        if not any('http://kgb.debian.net:9418/' in x.url for x in hooks):
            violations.add(project.name, f"WARNING: IRC notification (aka KGB) webhook missing")

    # services (aka integrations) checks

    if SALSA_TOKEN:
        services = project.services.list()
        services_titles = [x.title for x in services]
        if 'Emails on push' not in services_titles:
            violations.add(project.name, f"WARNING: email on push integration missing")
        if 'Irker (IRC gateway)' in services_titles:
            violations.add(project.name, f"WARNING: Irker integration still active, migrate to KGB webhook instead")

print(f"Report generated on: {datetime.datetime.now()}")
print(f'Total repositories processed: {len(group_projects)}\n')
violations.print()
