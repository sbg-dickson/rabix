import copy
import logging

import networkx as nx

from rabix.common.errors import ValidationError

log = logging.getLogger(__name__)


class Model(dict):
    """ Inherit this class for an easy way to turn JSON objects with $$type fields to classes. """
    TYPE = None

    def __init__(self, obj):
        super(dict, self).__init__()
        self.update(obj)

    def _validate(self):
        """ Override to validate. Raise AssertionError or ValidationError or return array of errors. Or return None. """
        pass

    def _check_field(self, field, field_type=None, null=True, look_in=None):
        obj = look_in or self
        assert field in obj, 'Must have a "%s" field' % field
        val = obj[field]
        if val is None:
            assert null, '%s cannot be null' % val
            return
        if field_type:
            assert isinstance(val, field_type), '%s is %s, expected %s' % (val, val.__class__.__name__, field_type)

    def validate(self):
        try:
            errors = self._validate()
        except AssertionError, e:
            raise ValidationError(unicode(e))
        if errors:
            raise ValidationError('. '.join(errors))

    def __json__(self):
        return dict({'$$type': self.TYPE}, **self)

    @classmethod
    def from_dict(cls, obj):
        return cls(obj)


class Pipeline(Model):
    TYPE = 'app/pipeline'

    apps = property(lambda self: self['apps'])
    steps = property(lambda self: self['steps'])

    def __init__(self, obj):
        super(Pipeline, self).__init__(obj)
        self.nx = None

    def draw(self, file_name='pipeline.png'):
        if not self.nx:
            self.validate()
        try:
            agr = nx.to_agraph(self.nx)
        except ImportError:
            log.error('Failed to import pygraphviz, cannot draw.')
            return
        agr.layout()
        agr.draw(file_name if file_name.endswith('png') else file_name+'.png')

    def _build_nx(self):
        g = nx.DiGraph()
        inputs, outputs = set(), set()
        for step in self.steps:
            g.add_node(step['id'], step=step, app=self.apps[step['app']])
            for inp_id, src_list in step.get('inputs', {}).iteritems():
                src_list = src_list if isinstance(src_list, list) else filter(None, [src_list])
                for src in src_list:
                    if '.' not in src:
                        inputs.add(src)
                        g.add_node(src, id=src, app='$$input')
                        conns = g.get_edge_data(src, step['id'], default={'conns': []})['conns']
                        conns.append([None, inp_id])
                        g.add_edge(src, step['id'], conns=conns)
                    else:
                        src_id, out_id = src.split('.')
                        conns = g.get_edge_data(src, step['id'], default={'conns': []})['conns']
                        conns.append([out_id, inp_id])
                        g.add_edge(src_id, step['id'], conns=conns)
            for out_id, dst_list in step.get('outputs', {}).iteritems():
                dst_list = dst_list if isinstance(dst_list, list) else filter(None, [dst_list])
                for dst in dst_list:
                    # assert '.' not in dst, 'output contains dot'
                    if '.' in dst:
                        continue
                    outputs.add(dst)
                    g.add_node(dst, id=dst, app='$$output')
                    conns = g.get_edge_data(step['id'], dst, default={'conns': []})['conns']
                    conns.append([out_id, None])
                    g.add_edge(step['id'], dst, conns=conns)
        step_ids = set(s['id'] for s in self.steps)
        assert not inputs.intersection(step_ids), 'Some inputs have same id as steps'
        assert not outputs.intersection(step_ids), 'Some outputs have same id as steps'
        assert nx.is_directed_acyclic_graph(g), 'Cycles in pipeline'
        self.nx = g
        return g

    def _validate(self):
        self._check_field('apps', dict, null=False)
        self._check_field('steps', list, null=False)
        for step in self.steps:
            self._check_field('id', basestring, null=False, look_in=step)
            self._check_field('app', basestring, null=False, look_in=step)
            assert step['app'] in self['apps'], '%s app not specified' % step['app']
        for app in self['apps'].itervalues():
            app.validate()
        assert self.apps, 'No apps'
        assert self.steps, 'No steps'
        self._build_nx()

    def get_app_for_step(self, step_or_id):
        if isinstance(step_or_id, basestring):
            step_or_id = filter(lambda s: s['id'] == step_or_id, self.steps)[0]
        return self['apps'][step_or_id['app']]

    def get_inputs(self):
        """
        Returns a dict that maps input names to dict objects obtained from the input schema of referenced apps.
        Incoming connections to steps with no '.' in identifier (not outputs of steps) are assumed to be inputs.

        Note: There is no validation performed here. Assuming pipeline is in the correct format and schema is valid.
        """
        to_list = lambda o: o if isinstance(o, list) else [] if o is None else [o]
        inputs = {}
        for step in self['steps']:
            for app_inp_id, incoming in step['inputs'].iteritems():
                incoming = to_list(incoming)
                for conn in incoming:
                    if '.' in conn:
                        continue
                    # look in the app to get the schema for this input
                    app_inputs = self['apps'][step['app']].schema.inputs
                    schema = filter(lambda i: i['id'] == app_inp_id, app_inputs)[0]
                    if conn not in inputs:
                        inputs[conn] = copy.deepcopy(schema)
                    else:
                        # Input goes to multiple steps, check again for list/required
                        inp = inputs[conn]
                        if schema.get('required'):
                            inp['required'] = True
                        if not schema.get('list'):
                            inp['list'] = False
        return inputs

    @classmethod
    def from_app(cls, app):
        if isinstance(app, Pipeline):
            return app
        pipeline = cls({
            'apps': app.apps,
            'steps': [{
                'id': app.apps.keys()[0],
                'app': app.apps.keys()[0],
                'inputs': {inp['id']: inp['id'] for inp in app.schema.inputs},
                'outputs': {out['id']: out['id'] for out in app.schema.outputs},
            }]
        })
        pipeline.validate()
        return pipeline


class App(Model):
    schema = property(lambda self: self['schema'])
    apps = property(lambda self: {'app': self})

    def get_inputs(self):
        return {inp['id']: inp for inp in self.schema.inputs}


class AppSchema(Model):
    TYPE = 'schema/app/sbgsdk'

    inputs = property(lambda self: self['inputs'])
    params = property(lambda self: self['params'])
    outputs = property(lambda self: self['outputs'])

    def _check_is_list_and_has_unique_ids(self, field, required_element_fields=None):
        self._check_field(field, list, null=False)
        for el in self[field]:
            for el_field in ['id'] + (required_element_fields or []):
                self._check_field(el_field, basestring, null=False, look_in=el)
        assert len(set(el['id'] for el in self[field])) == len(self[field]), '%s IDs must be unique' % field

    def _validate(self):
        self._check_is_list_and_has_unique_ids('inputs')
        self._check_is_list_and_has_unique_ids('outputs')
        self._check_is_list_and_has_unique_ids('params', required_element_fields=['type'])
