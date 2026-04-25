import { Routes } from '@angular/router';
import { LoginComponent } from './login/login.component';
import { RegistrationComponent } from './registration/registration.component';
import { AnnotationJobsWrapperComponent } from './annotation-jobs-wrapper/annotation-jobs-wrapper.component';
import { AboutPageComponent } from './about-page/about-page.component';
import {
  SingleAlleleAnnotationWrapperComponent
} from './single-allele-annotation-wrapper/single-allele-annotation-wrapper.component';

export const routes: Routes = [
  { path: 'annotation-jobs', component: AnnotationJobsWrapperComponent },
  { path: 'single-allele-annotation', component: SingleAlleleAnnotationWrapperComponent },
  { path: 'login', component: LoginComponent },
  { path: 'register', component: RegistrationComponent },
  { path: 'about', component: AboutPageComponent },
  { path: '**', redirectTo: 'single-allele-annotation' },
];
