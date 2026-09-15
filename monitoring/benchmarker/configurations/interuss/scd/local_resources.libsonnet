{
  auth: {
    utm_auth: {
      resource_type: 'resources.communications.AuthAdapterResource',
      specification: {
        auth_spec: 'DummyOAuth(http://localhost:8085/token,benchmarker)',
        scopes_authorized: [
          'utm.strategic_coordination',
        ],
      },
    },
  },

  per_uss_dss_instances: function(num_uss, num_nodes) {
    local nodeIndex = function(uss, node) std.format('%02d', node + num_nodes * (uss - 1)),

    ['uss%d_dss_pool' % uss]: {
      resource_type: 'resources.astm.f3548.v21.DSSInstancesResource',
      dependencies: {
        auth_adapter: 'utm_auth',
      },
      specification: {
        dss_instances: [
          {
            participant_id: 'uss%(uss)d_dss%(node)d' % { uss: uss, node: node },
            base_url: 'http://localhost:80%s' % nodeIndex(uss, node),
          } for node in std.range(1, num_nodes)
        ],
      },
    } for uss in std.range(1, num_uss)
  }
}
