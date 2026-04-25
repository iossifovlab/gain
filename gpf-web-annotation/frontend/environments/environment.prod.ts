import versionInfo from '../version.json';
const basePath = '';

export const environment = {
  production: true,
  basePath: basePath,
  apiPath: basePath + '/api',
  imgPathPrefix: '/assets',
  socketPath: '/ws',
  version: versionInfo?.version
};
