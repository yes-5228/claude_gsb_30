import { http } from './client.js';

const RESOURCE = '/inspections';

export const inspectionApi = {
  list: (params) => http.get(RESOURCE, params),
  detail: (id) => http.get(`${RESOURCE}/${id}`),
  create: (payload) => http.post(RESOURCE, payload),
  update: (id, payload, issueMode = 'adjust') =>
    http.patch(`${RESOURCE}/${id}`, payload, { issue_mode: issueMode }),
  remove: (id, issueMode = 'void') =>
    http.delete(`${RESOURCE}/${id}`, { issue_mode: issueMode }),
};
