# -*- coding: utf-8 -*-
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.

import datetime
import functools
import os

import flask
import taskcluster
from sqlalchemy.orm.exc import NoResultFound
from werkzeug.exceptions import BadRequest

from backend_common.auth0 import mozilla_accept_token
from cli_common.log import get_logger
from shipit_workflow.models import Phase
from shipit_workflow.models import Release
from shipit_workflow.tasks import UnsupportedFlavor
from shipit_workflow.tasks import fetch_actions_json
from shipit_workflow.tasks import generate_action_task
from shipit_workflow.tasks import render_action_task

log = get_logger(__name__)


def _queue():
    queue = taskcluster.Queue({
        'credentials': {
            'clientId': os.environ.get('TASKCLUSTER_CLIENT_ID'),
            'accessToken': os.environ.get('TASKCLUSTER_ACCESS_TOKEN')
        },
        'maxRetries': 12
    })
    return queue


def validate_user(key, checker):
    def wrapper(view_func):
        @functools.wraps(view_func)
        def decorated(*args, **kwargs):
            try:
                if checker(flask.g.userinfo[key]):
                    return view_func(*args, **kwargs)
            except (AttributeError, KeyError):
                response_body = {'error': 'missing_userinfo',
                                 'error_description': 'Userinfo is missing'}
                return response_body, 401, {'WWW-Authenticate': 'Bearer'}

            response_body = {'error': 'invalid_permissions',
                             'error_description': 'Check your permissions'}
            return response_body, 401, {'WWW-Authenticate': 'Bearer'}
        return decorated
    return wrapper


@mozilla_accept_token()
@validate_user(key='https://sso.mozilla.com/claim/groups',
               checker=lambda xs: 'releng' in xs)
def add_release(body):
    session = flask.g.db.session
    r = Release(
        product=body['product'],
        version=body['version'],
        branch=body['branch'],
        revision=body['revision'],
        build_number=body['build_number'],
        release_eta=body.get('release_eta'),
        status='scheduled',
        partial_updates=body.get('partial_updates')
    )
    try:
        r.generate_phases(
            partner_urls=flask.current_app.config.get('PARTNERS_URL'),
            github_token=flask.current_app.config.get('GITHUB_TOKEN'),
        )
        session.add(r)
        session.commit()
        return r.json, 201
    except UnsupportedFlavor as e:
        raise BadRequest(description=e.description)


def list_releases(product=None, branch=None, version=None, build_number=None,
                  status=['scheduled']):
    session = flask.g.db.session
    releases = session.query(Release)
    if product:
        releases = releases.filter(Release.product == product)
    if branch:
        releases = releases.filter(Release.branch == branch)
    if version:
        releases = releases.filter(Release.version == version)
        if build_number:
            releases = releases.filter(Release.build_number == build_number)
    elif build_number:
        raise BadRequest(description='Filtering by build_number without version'
                         ' is not supported.')
    releases = releases.filter(Release.status.in_(status))
    return [r.json for r in releases.all()]


def get_release(name):
    session = flask.g.db.session
    try:
        release = session.query(Release).filter(Release.name == name).one()
        return release.json
    except NoResultFound:
        flask.abort(404)


def get_phase(name, phase):
    session = flask.g.db.session
    try:
        phase = session.query(Phase) \
            .filter(Release.id == Phase.release_id) \
            .filter(Release.name == name) \
            .filter(Phase.name == phase).one()
        return phase.json
    except NoResultFound:
        flask.abort(404)


@mozilla_accept_token()
@validate_user(key='https://sso.mozilla.com/claim/groups',
               checker=lambda xs: 'releng' in xs)
def schedule_phase(name, phase):
    session = flask.g.db.session
    try:
        phase = session.query(Phase) \
            .filter(Release.id == Phase.release_id) \
            .filter(Release.name == name) \
            .filter(Phase.name == phase).one()
    except NoResultFound:
        flask.abort(404)

    if phase.submitted:
        flask.abort(409, 'Already submitted!')

    _queue().createTask(phase.task_id, phase.rendered)
    phase.submitted = True
    phase.completed_by = flask.g.userinfo['email']
    phase.completed = datetime.datetime.utcnow()
    if all([ph.submitted for ph in phase.release.phases]):
        phase.release.status = 'shipped'
    session.commit()
    return phase.json


@mozilla_accept_token()
@validate_user(key='https://sso.mozilla.com/claim/groups',
               checker=lambda xs: 'releng' in xs)
def abandon_release(name):
    session = flask.g.db.session
    try:
        release = session.query(Release).filter(Release.name == name).one()
        # Cancel all submitted task groups first
        for phase in filter(lambda x: x.submitted, release.phases):
            actions = fetch_actions_json(phase.task_id)
            action_task_id, action_task, context = generate_action_task(
                action_name='cancel-all',
                action_task_input={},
                actions=actions,
            )
            # some parameters contain a lot of entries, so we hit the payload
            # size limit. We don't use this parameter in any case, safe to
            # remove
            for long_param in ('existing_tasks', 'release_history', 'release_partner_config'):
                del context['parameters'][long_param]
            # ACTION_TASK_ID should be explicitly specified and be the original
            # action task that generated this phase.
            action_task = render_action_task(task=action_task, context=context,
                                             action_task_id=phase.task_id)
            # Add the initial action task to the list of dependencies to
            # prevent early firing
            action_task['dependencies'].append(phase.task_id)
            log.info('Cancel phase %s by task %s', phase.name, action_task_id)
            _queue().createTask(action_task_id, action_task)

        release.status = 'aborted'
        session.commit()
        return release.json
    except NoResultFound:
        flask.abort(404)
