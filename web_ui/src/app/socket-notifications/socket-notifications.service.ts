import { Injectable } from '@angular/core';
import {
  BehaviorSubject,
  catchError,
  filter,
  map,
  Observable,
  of,
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
  private maxReconnectionAttempts = 5;
  private isReconnecting = false;
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

  private retryAfterError<T>(): (source: Observable<T>) => Observable<T> {
    return source => source.pipe(
      catchError((err: unknown, caught) => {
        if (err instanceof CloseEvent) {
          return throwError(() => err);
        }
        if (err instanceof Event) {
          return timer(2000).pipe(switchMap(() => caught));
        }
        return throwError(() => err as Error);
      })
    );
  }

  public getJobNotifications(): Observable<JobNotification> {
    this.ensureConnected();
    return this.socket$.pipe(
      filter((ws): ws is WebSocketSubject<object> => ws !== null),
      switchMap(ws => ws.pipe(
        filter(n => n['type'] === 'job_status'),
        map((n: object) => JobNotification.fromJson(n)),
        this.retryAfterError()
      ))
    );
  }

  public getPipelineNotifications(): Observable<PipelineNotification> {
    this.ensureConnected();
    return this.socket$.pipe(
      filter((ws): ws is WebSocketSubject<object> => ws !== null),
      switchMap(ws => ws.pipe(
        filter(n => n['type'] === 'pipeline_status'),
        map((n: object) => PipelineNotification.fromJson(n)),
        this.retryAfterError()
      ))
    );
  }

  public reopenConnection(): Observable<void> {
    // A reconnection is already in flight: share it so concurrent callers
    // wait for the same attempt instead of spawning parallel ones.
    if (this.isReconnecting) {
      return this.pendingReconnection$ || of(undefined);
    }

    // Give up after too many consecutive attempts with no confirmed open.
    // The counter is reset by ensureConnected's openObserver, so once the
    // server comes back and a socket actually opens, reconnection recovers.
    if (this.reconnectionAttempts >= this.maxReconnectionAttempts) {
      console.error('Max reconnection attempts reached.');
      return throwError(() => new Error('Max reconnection attempts reached'));
    }

    // First reconnection attempt: 200ms (allow session sync in CI). Then
    // exponential backoff: 1s, 2s, 4s, etc. (max 10s).
    const delayMs = this.reconnectionAttempts === 0
      ? 200
      : Math.min(1000 * Math.pow(2, this.reconnectionAttempts - 1), 10000);
    this.reconnectionAttempts++;

    const reconnectObservable = timer(delayMs).pipe(
      tap(() => {
        // Create a new WebSocket without force-closing the old one; let the
        // old connection die naturally to avoid a "closed before connection
        // established" error. reconnectionAttempts is intentionally NOT reset
        // here — only a confirmed open (openObserver) resets it, so a
        // persistently-down server keeps backing off instead of hot-looping.
        this.socketNotifications = null;
        this.ensureConnected();
        this.isReconnecting = false;
        this.pendingReconnection$ = null;
      }),
      map(() => undefined as void), // Convert timer output to void
      catchError((err: unknown) => {
        this.isReconnecting = false;
        this.pendingReconnection$ = null;
        return throwError(() => err);
      }),
      shareReplay(1) // Share the observable across multiple subscribers
    );

    this.pendingReconnection$ = reconnectObservable;
    this.isReconnecting = true;

    return reconnectObservable;
  }
}
