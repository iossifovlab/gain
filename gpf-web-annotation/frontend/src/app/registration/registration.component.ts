import { Component, ElementRef, ViewChild } from '@angular/core';
import { UsersService } from '../users.service';
import { FormsModule } from '@angular/forms';
import { CommonModule } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Router, RouterModule } from '@angular/router';
import { environment } from '../../../environments/environment';

@Component({
  selector: 'app-registration',
  imports: [FormsModule, CommonModule, RouterModule],
  templateUrl: './registration.component.html',
  styleUrl: './registration.component.css'
})
export class RegistrationComponent {
  @ViewChild('emailInput') private email!: ElementRef;
  @ViewChild('passwordInput') private password!: ElementRef;
  public responseMessage: string = '';
  public validationMessage: string = '';
  public readonly environment = environment;

  public constructor(private usersService: UsersService, private router: Router) {}

  public register(): void {
    this.responseMessage = '';
    if (!this.isFormValid()) {
      return;
    }
    const email = (this.email.nativeElement as HTMLInputElement).value;
    const password = (this.password.nativeElement as HTMLInputElement).value;
    this.usersService.registerUser(email, password).subscribe({
      next: () => {
        this.responseMessage = 'Registration successful! Please check your email to verify your account.';
        this.cleanInputs();
        this.router.navigate(['/login']);
      },
      error: (error: HttpErrorResponse) => {
        this.responseMessage = (error.error as {error: string})['error'] || 'Registration failed!';
      }
    });
  }

  private validateEmail(): boolean {
    const email = (this.email.nativeElement as HTMLInputElement).value;
    const re = new RegExp(/^\w+([.-]?\w+)*@\w+([.-]?\w+)*(\.\w{2,3})+$/);
    return re.test(String(email).toLowerCase());
  }

  private validatePassword(): boolean {
    const password = (this.password.nativeElement as HTMLInputElement).value;
    const re = new RegExp(/.{6,}/);
    return re.test(String(password).toLowerCase());
  }

  private isFormValid(): boolean {
    const emailRef = this.email.nativeElement as HTMLInputElement;
    const passwordRef = this.password.nativeElement as HTMLInputElement;
    this.validationMessage = '';
    if (!emailRef.value) {
      emailRef.focus();
      return false;
    }
    if (!this.validateEmail()) {
      emailRef.focus();
      this.validationMessage = 'Invalid email format.';
      return false;
    }
    if (!passwordRef.value) {
      passwordRef.focus();
      return false;
    }
    if (!this.validatePassword()) {
      passwordRef.focus();
      this.validationMessage = 'Password must be at least 6 characters long.';
      return false;
    }
    return true;
  }

  private cleanInputs(): void {
    (this.email.nativeElement as HTMLInputElement).value = '';
    (this.password.nativeElement as HTMLInputElement).value = '';
  }
}
