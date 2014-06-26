import os
import tempfile
import unittest
import logging

from nose.tools import nottest, assert_equals, assert_raises

from rabix import CONFIG
from rabix.common import six
from rabix.common.protocol import Outputs, WrapperJob
from rabix.common.util import rnd_name
from rabix.common.errors import ValidationError
from rabix.runtime import from_url
from rabix.runtime.engine import get_engine
from rabix.runtime.engine.base import SequentialEngine
from rabix.runtime.engine.base import MultiprocessingEngine
from rabix.runtime.engine.rqengine import RQEngine
from rabix.runtime.jobs import RunJob

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


def load(path):
    with open(path) as fp:
        return fp.read()


def save(val):
    result = tempfile.mktemp(dir='.')
    with open(result, 'w') as fp:
        fp.write(six.text_type(val))
    return os.path.abspath(result)


def get_inp(d, inp, unpack=False, cast=None):
    # val = map(load, d.get('$inputs', {}).get(inp, []))
    val = [load(x) for x in d['$inputs'][inp]]
    if cast:
        val = [cast(x) for x in val]
    if unpack:
        val = val[0]
    return val


def generator(_):
    return Outputs({'generated': save(1)})


def incrementor(job):
    args = job.args
    num = get_inp(args, 'to_increment', True, int) or 0
    return Outputs({'incremented': save(num + 1)})


def two_step_increment(job):
    args = job.args
    step = args.get('$step', 0)
    if step == 0:
        num = get_inp(args, 'to_increment', True, int) or 0
        return WrapperJob(args={
            '$step': 2,
            'sum': [
                num,
                WrapperJob(args={'$step': 1}),
                WrapperJob(args={'$step': 1})
            ]
        })
    elif step == 1:
        return 1
    elif step == 2:
        return Outputs({'incremented': save(sum(args['sum']))})
    else:
        raise ValueError('Bad step %s' % step)


@nottest
def test_pipeline(pipeline_url, engine_cls=None):
    # Be warned, all dirs with this prefix will be rm -rf on success
    prefix = 'x-test-%s' % rnd_name(5)
    pipeline = from_url(pipeline_url)
    job = RunJob(prefix, pipeline, inputs={'number': 'data:,1'})
    sch = engine_cls() if engine_cls else get_engine()
    sch.run(job)
    assert_equals(job.status, RunJob.FINISHED)
    with open(job.get_outputs()['incremented'][0]) as fp:
        assert_equals(fp.read(), '4')
    if prefix.startswith('x-test-'):
        os.system('rm -rf %s.*' % prefix)


def test_mock_pipeline_sequential():
    test_pipeline('rabix/tests/apps/mocks/mock.pipeline.json', SequentialEngine)


def test_mock_pipeline_relative_refs():
    test_pipeline('rabix/tests/apps/mock_ref.json', SequentialEngine)


def test_mock_pipeline_mp():
    test_pipeline('rabix/tests/apps/mocks/mock.pipeline.json',
                  MultiprocessingEngine)


def test_mock_pipeline_remote_ref():
    test_pipeline('rabix/tests/apps/mocks/mock.pipeline.remote_ref.json')


# noinspection PyClassicStyleClass
class RQEngineTest(unittest.TestCase):
    def setUp(self):
        cmd = 'python -m rabix.runtime.run_workers --node-id x-test'
        if os.system(cmd):
            raise RuntimeError('Worker startup failed.')

    def test_mock_pipeline_rq(self):
        CONFIG['nodes'] = [{
            'node_id': 'x-test',
            'ram_mb': 500,
            'cpu': 2,
        }]
        test_pipeline('rabix/tests/apps/mocks/mock.pipeline.json', RQEngine)
        os.remove('supervisord.log')
        os.system('rm x-test-*')

    def tearDown(self):
        os.remove('supervisord.conf')
        os.system('kill $(cat supervisord.pid)')


def test_circular_ref():
    with assert_raises(ValidationError):
        from_url('rabix/tests/apps/mocks/circular.json')