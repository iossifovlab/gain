import { Injectable } from '@angular/core';
import {
  BehaviorSubject,
  concatWith,
  defer,
  filter,
  map,
  Observable,
  shareReplay,
  switchMap,
  tap,
  throwError,
  timer,
} from 'rxjs';
import { webSocket, WebSocketSubject } from 'rxjs/webSocket';
import { environment } from '../../../environments/environment';
import { JobNotification, PipelineNotification } from './socket-notifications';

@Injectable({
  providedIn: 'root'
})
export class SocketNotificationsService {
  public constructor() { }

  private readonly socketNotificationsUrl = `${environment.socketPath}/notifications`;
  private socketNotifications: WebSocketSubject<object> | null = null;
  private readonly socket$ = new BehaviorSubject<WebSocketSubject<object> | null>(null);
  private reconnectionAttempts = 0;
  // Attempts up to this many ramp through exponential backoff; beyond it the
  // service keeps retrying on a longer cooldown (never permanently refuses) so
  // a server that eventually returns still triggers a real open that resets
  // the counter -- no page reload required.
  private readonly maxReconnectionAttempts = 5;
  private readonly reconnectionCooldownMs = 30000;
  private pendingReconnection$: Observable<void> | null = null;

  public ensureConnected(): void {
    if (!this.socketNotifications) {
      this.socketNotifications = webSocket({
        url: this.socketNotificationsUrl,
        openObserver: {
          // Only a confirmed connection resets the backoff counter.
          // webSocket() connects lazily, so resetting anywhere else
          // (e.g. on socket creation) would defeat the backoff entirely.
          next: () => {
            this.reconnectionAttempts = 0;
          },
        },
      });
      this.socket$.next(this.socketNotifications);
    }
  }

  private reconnectOnClose<T>(): (source: Observable<T>) => Observable<T> {
    return source => source.pipe(
      // Every disconnect signal is routed to the single consumer-driven
      // reconnect backoff. Error closes (CloseEvent for an unclean drop, Event
      // for the common abnormal-drop path) propagate untouched. A GRACEFUL
      // server close instead completes the inner socket -- switchMap over the
      // socket$ BehaviorSubject would swallow that completion and silently stop
      // notifications, so convert it into the same CloseEvent the error path
      // emits. A genuine consumer unsubscribe tears the source down without
      // completing, so concatWith does not fire spuriously.
      concatWith(throwError(() => new CloseEvent('close')))
    );
  }

  public getJobNotifications(): Observable<JobNotification> {
    return this.socket$.pipe(
      filter((ws): ws is WebSocketSubject<object> => ws !== null),
      switchMap(ws => ws.pipe(
        filter(n => n['type'] === 'job_status'),
        map((n: object) => JobNotification.fromJson(n)),
        this.reconnectOnClose()
      ))
    );
  }

  public getPipelineNotifications(): Observable<PipelineNotification> {
    return this.socket$.pipe(
      filter((ws): ws is WebSocketSubject<object> => ws !== null),
      switchMap(ws => ws.pipe(
        filter(n => n['type'] === 'pipeline_status'),
        map((n: object) => PipelineNotification.fromJson(n)),
        this.reconnectOnClose()
      ))
    );
  }

  public reopenConnection(): Observable<void> {
    // Lazy: an attempt -- and its backoff delay -- starts only on subscribe.
    return defer(() => {
      // A reconnection is already in flight: share it so concurrent callers
      // wait for the same attempt instead of spawning parallel ones.
      if (this.pendingReconnection$) {
        return this.pendingReconnection$;
      }

      const delayMs = this.nextReconnectDelayMs();
      this.reconnectionAttempts++;

      this.pendingReconnection$ = timer(delayMs).pipe(
        tap(() => {
          // Release the slot first, before subscribers are notified and before
          // ensureConnected() can throw, so any reconnect from here on --
          // including after a failed attempt -- starts a fresh, backed-off one.
          this.pendingReconnection$ = null;
          // Create a new WebSocket without force-closing the old one; let the
          // old connection die naturally to avoid a "closed before connection
          // established" error. Only a confirmed open (openObserver) resets
          // reconnectionAttempts, so a down server keeps backing off.
          this.socketNotifications = null;
          this.ensureConnected();
        }),
        map(() => undefined as void), // Convert timer output to void
        // Share the attempt among its joiners and keep the timer running even
        // if they all unsubscribe, so the slot is always released.
        shareReplay(1)
      );
      return this.pendingReconnection$;
    });
  }

  private nextReconnectDelayMs(): number {
    // First reconnection attempt: 200ms (allow session sync in CI). Then
    // exponential backoff: 1s, 2s, 4s, 8s (max 10s). Past the cap, keep
    // retrying on a longer cooldown rather than refusing, so a server that
    // eventually returns still yields a real open that resets the counter.
    if (this.reconnectionAttempts === 0) {
      return 200;
    }
    if (this.reconnectionAttempts >= this.maxReconnectionAttempts) {
      return this.reconnectionCooldownMs;
    }
    return Math.min(1000 * Math.pow(2, this.reconnectionAttempts - 1), 10000);
  }
}
