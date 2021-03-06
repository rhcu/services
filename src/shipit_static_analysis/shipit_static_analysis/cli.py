# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import click

from cli_common.cli import taskcluster_options
from cli_common.log import get_logger
from cli_common.log import init_logger
from cli_common.taskcluster import get_secrets
from cli_common.taskcluster import get_service
from shipit_static_analysis import config
from shipit_static_analysis import stats
from shipit_static_analysis.config import settings
from shipit_static_analysis.report import get_reporters
from shipit_static_analysis.revisions import MozReviewRevision
from shipit_static_analysis.revisions import PhabricatorRevision
from shipit_static_analysis.workflow import Workflow

logger = get_logger(__name__)


@click.command()
@taskcluster_options
@click.option(
    '--source',
    envvar='ANALYSIS_SOURCE',
)
@click.option(
    '--id',
    envvar='ANALYSIS_ID',
)
@click.option(
    '--mozreview-diffset',
    envvar='MOZREVIEW_DIFFSET',
)
@click.option(
    '--mozreview-revision',
    envvar='MOZREVIEW_REVISION',
)
@click.option(
    '--cache-root',
    required=True,
    help='Cache root, used to pull changesets'
)
@stats.api.timer('runtime.analysis')
def main(source,
         id,
         cache_root,
         mozreview_diffset,
         mozreview_revision,
         taskcluster_secret,
         taskcluster_client_id,
         taskcluster_access_token,
         ):

    secrets = get_secrets(taskcluster_secret,
                          config.PROJECT_NAME,
                          required=(
                              'APP_CHANNEL',
                              'REPORTERS',
                              'ANALYZERS',
                          ),
                          existing={
                              'APP_CHANNEL': 'development',
                              'REPORTERS': [],
                              'ANALYZERS': ['clang-tidy', ],
                              'PUBLICATION': 'IN_PATCH',
                          },
                          taskcluster_client_id=taskcluster_client_id,
                          taskcluster_access_token=taskcluster_access_token,
                          )

    init_logger(config.PROJECT_NAME,
                PAPERTRAIL_HOST=secrets.get('PAPERTRAIL_HOST'),
                PAPERTRAIL_PORT=secrets.get('PAPERTRAIL_PORT'),
                SENTRY_DSN=secrets.get('SENTRY_DSN'),
                MOZDEF=secrets.get('MOZDEF'),
                )

    # Setup settings before stats
    settings.setup(secrets['APP_CHANNEL'], cache_root, secrets['PUBLICATION'])

    # Setup statistics
    datadog_api_key = secrets.get('DATADOG_API_KEY')
    if datadog_api_key:
        stats.auth(datadog_api_key)

    # Load reporters
    reporters = get_reporters(
        secrets['REPORTERS'],
        taskcluster_client_id,
        taskcluster_access_token,
    )

    # Load index service
    index_service = get_service(
        'index',
        taskcluster_client_id,
        taskcluster_access_token,
    )

    # Load unique revision
    if source == 'phabricator':
        api = reporters.get('phabricator')
        assert api is not None, \
            'Cannot use a phabricator revision without a phabricator reporter'
        revision = PhabricatorRevision(id, api)

    elif source == 'mozreview':
        revision = MozReviewRevision(id, mozreview_revision, mozreview_diffset)

    else:
        raise Exception('Unsupported analysis source: {}'.format(source))

    w = Workflow(reporters, secrets['ANALYZERS'], index_service)
    try:
        w.run(revision)
    except Exception as e:
        # Log errors to papertrail
        logger.error(
            'Static analysis failure',
            revision=revision,
            error=e,
        )

        # Index analysis state
        w.index(revision, state='error')

        # Then raise to mark task as erroneous
        raise


if __name__ == '__main__':
    main()
