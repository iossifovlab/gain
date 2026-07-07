import { ChangeDetectorRef, Component, DoCheck, inject, OnDestroy, OnInit } from '@angular/core';
import { Router, RouterModule, RouterOutlet } from '@angular/router';
import { UsersService } from './users.service';
import { UserData } from './users';
import { filter, distinctUntilChanged, Subscription } from 'rxjs';
import { environment } from '../../environments/environment';
import { MarkdownModule } from 'ngx-markdown';
import { SocketNotificationsService } from './socket-notifications/socket-notifications.service';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterModule, MarkdownModule],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
})
export class AppComponent implements DoCheck, OnInit, OnDestroy {
  public currentUserData: UserData = null;
  public readonly environment = environment;
  public menuOpen = false;
  private userDataSubscription: Subscription = new Subscription();
  private firstUserDataLoad = true;

  private readonly usersService = inject(UsersService);
  private readonly changeDetectorRef = inject(ChangeDetectorRef);
  private readonly socketNotificationsService = inject(SocketNotificationsService);
  private readonly router = inject(Router);

  public ngOnInit(): void {
    this.usersService.refreshUserData();
    this.userDataSubscription = this.usersService.userData.pipe(
      filter(userData => userData !== null),
      distinctUntilChanged((a, b) => a?.email === b?.email),
    ).subscribe((userData) => {
      if (!this.firstUserDataLoad) {
        this.socketNotificationsService.reopenConnection().subscribe({
          next: () => { /* socket reopened */ },
          error: (e) => console.error('Failed to reopen socket:', e)
        });
      } else {
        // On the first user load, make sure the socket is (lazily) connected.
        // Consumers also open it on route mount, so this is a best-effort
        // early connect, not an auth/session precondition.
        this.socketNotificationsService.ensureConnected();
      }
      this.currentUserData = userData;
      this.firstUserDataLoad = false;
    });
  }

  public ngDoCheck(): void {
    this.changeDetectorRef.detectChanges();
  }

  public ngOnDestroy(): void {
    this.userDataSubscription.unsubscribe();
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
