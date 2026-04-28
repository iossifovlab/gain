import { Component, ElementRef, OnInit, ViewChild } from '@angular/core';
import { UsersService } from '../users.service';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { RouterModule, Router, ActivatedRoute } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { environment } from '../../../environments/environment';
import { map, take } from 'rxjs';

@Component({
  selector: 'app-login',
  imports: [FormsModule, CommonModule, RouterModule],
  templateUrl: './login.component.html',
  styleUrl: './login.component.css',
})
export class LoginComponent implements OnInit {
  @ViewChild('emailInput') private email!: ElementRef;
  @ViewChild('passwordInput') private password!: ElementRef;
  public responseMessage: string = '';
  public readonly environment = environment;
  public readonly resetPasswordUrl = `${environment.apiPath}/forgotten_password`;
  public activationStatus: '' | 'successful' | 'failed';

  public constructor(
    private usersService: UsersService,
    private router: Router,
    private route: ActivatedRoute,
  ) {}

  public ngOnInit(): void {
    this.route.queryParamMap.pipe(
      map(params => params.get('activation_successful')),
      take(1)
    ).subscribe((status) => {
      if (status === null) {
        this.activationStatus = '';
      } else if (status === 'True') {
        this.activationStatus = 'successful';
      } else {
        this.activationStatus = 'failed';
      }
    });
  }

  public login(): void {
    this.responseMessage = '';
    const email = (this.email.nativeElement as HTMLInputElement).value;
    const password = (this.password.nativeElement as HTMLInputElement).value;
    this.usersService.loginUser(email, password).subscribe({
      next: () => {
        this.cleanInputs();
        this.router.navigate(['/single-annotation']);
      },
      error: (error: HttpErrorResponse) => {
        this.responseMessage = (error.error as {error: string})['error'] || 'Login failed!';
      }
    });
  }

  private cleanInputs(): void {
    (this.email.nativeElement as HTMLInputElement).value = '';
    (this.password.nativeElement as HTMLInputElement).value = '';
  }
}
