import os
import tempfile
import unittest
import subprocess

from nose.tools import nottest, assert_equals

from rabix.common.protocol import Outputs, WrapperJob
from rabix.common.util import rnd_name
from rabix.runtime import from_url
from rabix.runtime.engine import get_engine, SequentialEngine, MultiprocessingEngine, RQEngine
from rabix.runtime.jobs import RunJob


def load(path):
    with open(path) as fp:
        return fp.read()


def save(val):
    result = tempfile.mktemp(dir='.')
    with open(result, 'w') as fp:
        fp.write(unicode(val))
    return os.path.abspath(result)


def get_inp(d, inp, unpack=False, cast=None):
    # val = map(load, d.get('$inputs', {}).get(inp, []))
    val = map(load, d['$inputs'][inp])
    if cast:
        val = map(cast, val)
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
        return WrapperJob(args={'$step': 2, 'sum': [num, WrapperJob(args={'$step': 1}), WrapperJob(args={'$step': 1})]})
    elif step == 1:
        return 1
    elif step == 2:
        return Outputs({'incremented': save(sum(args['sum']))})
    else:
        raise ValueError('Bad step %s' % step)


@nottest
def test_pipeline(pipeline_name, engine_cls=None):
    prefix = 'x-test-%s' % rnd_name(5)  # Be warned, all dirs with this prefix will be rm -rf on success
    pipeline = from_url(get_mock_pipeline(pipeline_name))
    job = RunJob(prefix, pipeline, inputs={'number': 'data:,1'})
    sch = engine_cls() if engine_cls else get_engine()
    sch.run(job)
    assert_equals(job.status, RunJob.FINISHED)
    with open(job.get_outputs()['incremented'][0]) as fp:
        assert_equals(fp.read(), '4')
    if prefix.startswith('x-test-'):
        os.system('rm -rf %s.*' % prefix)


def get_mock_pipeline(name):
    return os.path.join(os.path.dirname(__file__), 'apps', name)


def test_mock_pipeline_sequential():
    test_pipeline('mock.pipeline.json', SequentialEngine)


def test_mock_pipeline_mp():
    test_pipeline('mock.pipeline.json', MultiprocessingEngine)


@nottest
def test_mock_pipeline_rq():
    test_pipeline('mock.pipeline.json', RQEngine)


def test_mock_pipeline_remote_ref():
    test_pipeline('mock.pipeline.remote_ref.json')


# noinspection PyClassicStyleClass
class RQTest(unittest.TestCase):
    def setUp(self):
        self.proc1 = subprocess.Popen(['rqworker'])
        self.proc2 = subprocess.Popen(['rqworker'])

    def test_mock_pipeline_rq(self):
        test_pipeline('mock.pipeline.json', RQEngine)

    def tearDown(self):
        self.proc1.terminate()
        self.proc2.terminate()
