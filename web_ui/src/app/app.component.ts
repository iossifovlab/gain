
import { ChangeDetectorRef, Component, DoCheck, inject, OnInit } from '@angular/core';
import { Router, RouterModule, RouterOutlet } from '@angular/router';
import { UsersService } from './users.service';
import { UserData } from './users';
import { filter, takeWhile } from 'rxjs';
import { environment } from '../../environments/environment';
import { MarkdownModule } from 'ngx-markdown';
import { SocketNotificationsService } from './socket-notifications/socket-notifications.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterModule, MarkdownModule],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
})
export class AppComponent implements DoCheck, OnInit {
  public currentUserData: UserData = null;
  public readonly environment = environment;
  public menuOpen = false;

  private readonly usersService = inject(UsersService);
  private readonly changeDetectorRef = inject(ChangeDetectorRef);
  private readonly socketNotificationsService = inject(SocketNotificationsService);
  private readonly router = inject(Router);

  public ngOnInit(): void {
    this.usersService.refreshUserData();
  }

  public ngDoCheck(): void {
    this.usersService.userData.pipe(
      filter(userData => userData !== null),
      takeWhile(user => user?.email !== this.currentUserData?.email),
    ).subscribe((userData) => {
      this.currentUserData = userData;
      this.socketNotificationsService.reopenConnection();
    });
    this.changeDetectorRef.detectChanges();
  }

  public logout(): void {
    this.usersService.logout().subscribe(() => {
      this.currentUserData = null;
      window.location.reload();
      this.usersService.refreshUserData();
    });
  }

  public login(): void {
    this.router.navigate(['/login']);
  }

  public register(): void {
    this.router.navigate(['/register']);
  }

  public isAppHeaderVisible(): boolean {
    return !this.router.url.includes('login') && !this.router.url.includes('register');
  }
}
