import { Routes } from '@angular/router';
import { LoginComponent } from './login/login.component';
import { RegistrationComponent } from './registration/registration.component';
import { AnnotationJobsWrapperComponent } from './annotation-jobs-wrapper/annotation-jobs-wrapper.component';
import { AboutPageComponent } from './about-page/about-page.component';
import {
  SingleAnnotationWrapperComponent
} from './single-annotation-wrapper/single-annotation-wrapper.component';
import { UserQuotasComponent } from './user-quotas/user-quotas.component';

export const routes: Routes = [
  { path: 'annotation-jobs', component: AnnotationJobsWrapperComponent },
  { path: 'single-annotation', component: SingleAnnotationWrapperComponent },
  { path: 'login', component: LoginComponent },
  { path: 'register', component: RegistrationComponent },
  { path: 'about', component: AboutPageComponent },
  { path: 'quotas', component: UserQuotasComponent },
  { path: '**', redirectTo: 'single-annotation' },
];
