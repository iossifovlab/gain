
import { Observable, Subject, firstValueFrom } from 'rxjs';
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
    jest.useRealTimers();
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

  it('should propagate non-retryable errors for job notifications', async() => {
    const error = new Error('connection error');
    const resultPromise = firstValueFrom(service.getJobNotifications());
    subject.error(error);
    await expect(resultPromise).rejects.toThrow('connection error');
  });

  it('should propagate non-retryable errors for pipeline notifications', async() => {
    const error = new Error('connection error');
    const resultPromise = firstValueFrom(service.getPipelineNotifications());
    subject.error(error);
    await expect(resultPromise).rejects.toThrow('connection error');
  });

  describe('retry behavior', () => {
    function makeMultiSubscriptionWs(): { subjects: Subject<object>[] } {
      const subjects: Subject<object>[] = [];
      const mockWs = new Observable<object>(subscriber => {
        const s = new Subject<object>();
        subjects.push(s);
        const sub = s.subscribe(subscriber);
        return (): void => sub.unsubscribe();
      });
      (webSocket as unknown as jest.Mock).mockReturnValue(mockWs);
      service = new SocketNotificationsService();
      return { subjects: subjects };
    }

    it('should propagate CloseEvent for job notifications', () => {
      const { subjects } = makeMultiSubscriptionWs();
      const errorSpy = jest.fn();

      service.getJobNotifications().subscribe({ error: errorSpy });
      const closeEvent = new CloseEvent('close');
      subjects[0].error(closeEvent);

      expect(errorSpy).toHaveBeenCalledWith(closeEvent);
    });

    it('should propagate CloseEvent for pipeline notifications', () => {
      const { subjects } = makeMultiSubscriptionWs();
      const errorSpy = jest.fn();

      service.getPipelineNotifications().subscribe({ error: errorSpy });
      const closeEvent = new CloseEvent('close');
      subjects[0].error(closeEvent);

      expect(errorSpy).toHaveBeenCalledWith(closeEvent);
    });

    it('should not propagate Event error and retry after 2000ms', () => {
      jest.useFakeTimers();
      const { subjects } = makeMultiSubscriptionWs();
      const errorSpy = jest.fn();

      service.getJobNotifications().subscribe({ error: errorSpy });
      subjects[0].error(new Event('error'));

      expect(errorSpy).not.toHaveBeenCalled();
      expect(subjects).toHaveLength(1);

      jest.advanceTimersByTime(2000);

      expect(subjects).toHaveLength(2);
      expect(errorSpy).not.toHaveBeenCalled();
    });

    it('should not retry Event error before 2000ms have elapsed', () => {
      jest.useFakeTimers();
      const { subjects } = makeMultiSubscriptionWs();

      service.getJobNotifications().subscribe();
      subjects[0].error(new Event('error'));

      jest.advanceTimersByTime(1999);
      expect(subjects).toHaveLength(1);
    });

    it('should allow manual reconnection after CloseEvent', () => {
      const { subjects } = makeMultiSubscriptionWs();
      const job1 = new JobNotification(1, 'success');
      jest.spyOn(JobNotification, 'fromJson')
        .mockReturnValueOnce(job1);

      const values: JobNotification[] = [];
      const errorSpy = jest.fn();
      service.getJobNotifications().subscribe({
        next: v => values.push(v),
        error: errorSpy
      });

      // eslint-disable-next-line camelcase
      subjects[0].next({ type: 'job_status', job_id: 1, status: 3 });
      const closeEvent = new CloseEvent('close');
      subjects[0].error(closeEvent);

      expect(values).toStrictEqual([job1]);
      expect(errorSpy).toHaveBeenCalledWith(closeEvent);
    });
  });
});

describe('JobNotification', () => {
  beforeAll(() => {
    jest.restoreAllMocks();
  });

  it('should return undefined when fromJson is called with null', () => {
    expect(JobNotification.fromJson(null)).toBeUndefined();
  });

  it('should create a JobNotification from valid json', () => {
    // eslint-disable-next-line camelcase
    const result = JobNotification.fromJson({job_id: 42, status: 'success'});
    expect(result).toStrictEqual(new JobNotification(42, 'success'));
  });
});

describe('PipelineNotification', () => {
  beforeAll(() => {
    jest.restoreAllMocks();
  });

  it('should return undefined when fromJson is called with null', () => {
    expect(PipelineNotification.fromJson(null)).toBeUndefined();
  });

  it('should create a PipelineNotification from valid json', () => {
    // eslint-disable-next-line camelcase
    const result = PipelineNotification.fromJson({pipeline_id: 7, status: 'loaded'});
    expect(result).toStrictEqual(new PipelineNotification('7', 'loaded'));
  });
});
