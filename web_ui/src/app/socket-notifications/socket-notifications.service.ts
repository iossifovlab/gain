import { Injectable } from '@angular/core';
import { filter, map, Observable } from 'rxjs';
import { webSocket, WebSocketSubject } from 'rxjs/webSocket';
import { environment } from '../../../environments/environment';
import { JobNotification, PipelineNotification } from './socket-notifications';

@Injectable({
  providedIn: 'root'
})
export class SocketNotificationsService {
  public constructor() { }

  private readonly socketNotificationsUrl = `${environment.socketPath}/notifications`;
  private socketNotifications: WebSocketSubject<object> = webSocket(this.socketNotificationsUrl);

  public getJobNotifications(): Observable<JobNotification> {
    return this.socketNotifications.pipe(
      filter(n => n['type'] === 'job_status'),
      map((n: object) => JobNotification.fromJson(n))
    );
  }

  public getPipelineNotifications(): Observable<PipelineNotification> {
    return this.socketNotifications.pipe(
      filter(n => n['type'] === 'pipeline_status'),
      map((n: object) => PipelineNotification.fromJson(n))
    );
  }

  public reopenConnection(): void {
    this.closeConnection();
    this.socketNotifications = webSocket(this.socketNotificationsUrl);
  }

  public closeConnection(): void {
    this.socketNotifications?.complete();
    this.socketNotifications = null;
  }
}
