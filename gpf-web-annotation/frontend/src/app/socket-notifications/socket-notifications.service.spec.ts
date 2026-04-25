
import { Subject, firstValueFrom } from 'rxjs';
import { webSocket } from 'rxjs/webSocket';
import { SocketNotificationsService } from './socket-notifications.service';
import { JobNotification, PipelineNotification } from './socket-notifications';

jest.mock('rxjs/webSocket', () => ({
  webSocket: jest.fn()
}));

describe('SocketNotificationsService', () => {
  let subject: Subject<object>;
  let service: SocketNotificationsService;

  beforeEach(() => {
    subject = new Subject<object>();
    (webSocket as unknown as jest.Mock).mockReturnValue(subject);
    service = new SocketNotificationsService();
  });

  afterEach(() => {
    jest.resetAllMocks();
  });

  it('should get job failed notification', async() => {
    // eslint-disable-next-line camelcase
    const payloadJobFail = { type: 'job_status', job_id: 123, status: 4 };
    const convertedFail = new JobNotification(123, 'failed');
    const spy = jest.spyOn(JobNotification, 'fromJson').mockReturnValue(convertedFail);

    const resultPromise = firstValueFrom(service.getJobNotifications());
    subject.next(payloadJobFail);
    const result = await resultPromise;

    expect(spy).toHaveBeenCalledWith(payloadJobFail);
    expect(result).toBe(convertedFail);
  });


  it('should get job notifications only', async() => {
    const payloadIgnored = { type: 'other', foo: 'bar' };
    // eslint-disable-next-line camelcase
    const payloadJobSuccess = { type: 'job_status', job_id: 122, status: 3 };

    const convertedSuccess = new JobNotification(122, 'success');
    const spy = jest.spyOn(JobNotification, 'fromJson').mockReturnValue(convertedSuccess);

    const resultPromise = firstValueFrom(service.getJobNotifications());

    // push an ignored message first
    subject.next(payloadIgnored);
    // push a job message which should be emitted
    subject.next(payloadJobSuccess);
    const result = await resultPromise;

    expect(spy).toHaveBeenCalledWith(payloadJobSuccess);
    expect(result).toBe(convertedSuccess);
  });

  it('gets pipeline notifications', async() => {
    const payloadIgnored = { type: 'whatever' };
    // eslint-disable-next-line camelcase
    const payloadPipeline = { type: 'pipeline_status', status: 'loading', pipeline_id: 'p1' };

    const converted = new PipelineNotification('p1', 'loading');
    const spy = jest.spyOn(PipelineNotification, 'fromJson').mockReturnValue(converted);

    const resultPromise = firstValueFrom(service.getPipelineNotifications());

    subject.next(payloadIgnored);
    subject.next(payloadPipeline);

    const result = await resultPromise;

    expect(spy).toHaveBeenCalledWith(payloadPipeline);
    expect(result).toBe(converted);
  });

  it('calls complete on the underlying WebSocketSubject when closeConnection is called', () => {
    const completeSpy = jest.spyOn(subject, 'complete');

    service.getJobNotifications();
    service.closeConnection();

    expect(service['socketNotifications']).toBeNull();
    expect(completeSpy).toHaveBeenCalledWith();
  });
});