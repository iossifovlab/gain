import { test, expect } from '@playwright/test';
import * as utils from '../utils';


test.describe('Basic tests', () => {
  test('should check visible elements of login page', async({ page }) => {
    await page.goto('/login', {waitUntil: 'load'});
    await expect(page.locator('#front-page-container')).toBeVisible();
    await expect(page.locator('#login-container')).toBeVisible();
    await expect(page.locator('#email')).toBeVisible();
    await expect(page.locator('#password')).toBeVisible();
    await expect(page.locator('#register-link')).toBeVisible();
    await expect(page.locator('#login-container').getByRole('button', { name: 'Login' })).toBeVisible();
    await expect(page.locator('#divider')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Continue with Google'})).toBeVisible();
  });

  test('should check application description', async({ page }) => {
    await page.goto('/login', {waitUntil: 'load'});
    await expect(page.locator('#front-page-header p')).toHaveText('GAIn Web description');
  });

  test('should check url after navigating to register page from login page', async({ page }) => {
    await page.goto('/login', {waitUntil: 'load'});
    await page.locator('#register-link').click();
    expect(page.url()).toContain('/register');
  });

  test('should check if user is redirected to login page after clicking the link in register page', async({ page }) => {
    await page.goto('/register', {waitUntil: 'load'});
    await page.locator('#login-link').click();
    await expect(page.locator('app-login')).toBeVisible();
    expect(page.url()).toContain('/login');
  });
});

test.describe('Registration tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/register');
  });

  test('should successfully create user', async({ page }) => {
    const randomEmail = `${utils.getRandomString()}@email.com`;
    await page.locator('#email').pressSequentially(randomEmail);
    await page.locator('#password').pressSequentially('password123');
    await page.getByRole('button', { name: 'Create' }).click();
    await expect(page.locator('app-login')).toBeVisible();
  });

  test('should not create user with invalid email', async({ page }) => {
    await page.locator('#email').pressSequentially('user');
    await page.locator('#password').pressSequentially('password123');
    await page.getByRole('button', { name: 'Create' }).click();
    await expect(page.getByText('Invalid email format.')).toBeVisible();
    await expect(page.getByText('Invalid email format.')).toHaveCSS('color', 'rgb(255, 0, 0)');
    await expect(page.getByRole('textbox', { name: 'email' })).toHaveValue('user');
    await expect(page.getByRole('textbox', { name: 'email' })).toBeFocused();
    await expect(page.locator('#password')).toHaveValue('password123');
  });

  test('should not create user with invalid password', async({ page }) => {
    await page.locator('#email').pressSequentially('user@email.com');
    await page.locator('#password').pressSequentially('pa');
    await page.getByRole('button', { name: 'Create' }).click();
    await expect(page.getByText('Password must be at least 6 characters long.')).toBeVisible();
    await expect(page.getByText('Password must be at least 6 characters long.')).toHaveCSS('color', 'rgb(255, 0, 0)');
    await expect(page.locator('#email')).toHaveValue('user@email.com');
    await expect(page.locator('#password')).toBeFocused();
    await expect(page.locator('#password')).toHaveValue('pa');
  });

  test('should not create existing user', async({ page }) => {
    const randomEmail = `${utils.getRandomString()}@email.com`;
    await utils.registerUser(page, randomEmail, 'password123');

    await page.goto('/register', {waitUntil: 'load'});

    await page.locator('#email').pressSequentially(randomEmail);
    await page.locator('#password').pressSequentially('password123');
    await page.getByRole('button', { name: 'Create' }).click();
    await expect(page.getByText('This email is already in use')).toBeVisible();
    await expect(page.getByText('This email is already in use')).toHaveCSS('color', 'rgb(255, 0, 0)');
    await expect(page.locator('#email')).toHaveValue(randomEmail);
    await expect(page.locator('#password')).toHaveValue('password123');
  });

  test('should register and login after that', async({ page }) => {
    const randomEmail = `${utils.getRandomString()}@email.com`;
    await utils.registerUser(page, randomEmail, 'password123');

    await page.locator('#email').pressSequentially(randomEmail);
    await page.locator('#password').pressSequentially('password123');
    await page.locator('#login-container').getByRole('button', { name: 'Login' }).click();
    await page.waitForSelector('app-single-allele-annotation-wrapper', {timeout: 120000});
  });
});

test.describe('Login tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/login', {waitUntil: 'load'});
  });

  test('should check if jobs table page is visible after successful login', async({ page }) => {
    const randomEmail = `${utils.getRandomString()}@email.com`;
    await utils.registerUser(page, randomEmail, 'password123'); // need to register user first

    await page.locator('#email').pressSequentially(randomEmail);
    await page.locator('#password').pressSequentially('password123');
    await page.locator('#login-container').getByRole('button', { name: 'Login' }).click();
    await page.waitForSelector('app-single-allele-annotation-wrapper', {timeout: 120000});
    await expect(page.locator('#user-data')).toBeVisible();
  });

  test('should check if user is redirected to default page after logout', async({ page }) => {
    const randomEmail = `${utils.getRandomString()}@email.com`;
    await utils.registerUser(page, randomEmail, 'password123');

    await page.locator('#email').pressSequentially(randomEmail);
    await page.locator('#password').pressSequentially('password123');
    await page.locator('#login-container').getByRole('button', { name: 'Login' }).click();
    await page.waitForSelector('app-single-allele-annotation-wrapper', {timeout: 120000});

    await page.getByRole('button', { name: 'Logout' }).click();
    await page.waitForSelector('app-single-allele-annotation-wrapper', {timeout: 120000});
  });

  test('should show error message when trying to login with invalid credentials', async({ page }) => {
    await page.locator('#email').pressSequentially('nonexistent@email.com');
    await page.locator('#password').pressSequentially('nonexistentpassword');
    await page.locator('#login-container').getByRole('button', { name: 'Login' }).click();
    await expect(page.getByText('Invalid login credentials')).toBeVisible();
    await expect(page.locator('app-single-allele-annotation-wrapper')).not.toBeVisible();
  });

  test('should show error message when trying to login without email', async({ page }) => {
    await page.locator('#password').pressSequentially('nonexistentpassword');
    await page.locator('#login-container').getByRole('button', { name: 'Login' }).click();
    await expect(page.getByText('Invalid login credentials')).toBeVisible();
    await expect(page.locator('app-single-allele-annotation-wrapper')).not.toBeVisible();
  });

  test('should show error message when trying to login without password', async({ page }) => {
    await page.locator('#email').pressSequentially('nonexistent@email.com');
    await page.locator('#login-container').getByRole('button', { name: 'Login' }).click();
    await expect(page.getByText('Invalid login credentials')).toBeVisible();
    await expect(page.locator('app-single-allele-annotation-wrapper')).not.toBeVisible();
  });

  test('should reset password for user and then login', async({ page }) => {
    const randomEmail = `${utils.getRandomString()}@email.com`;
    await utils.registerUser(page, randomEmail, 'password123'); // need to register user first

    await page.locator('#email').pressSequentially(randomEmail);
    await page.locator('#password').pressSequentially('password123');
    await page.locator('#login-container').getByRole('button', { name: 'Login' }).click();
    await page.waitForSelector('app-single-allele-annotation-wrapper', {timeout: 120000});

    await page.locator('#logout-button').click();
    await page.waitForSelector('#login-button');
    await page.locator('#login-button').click();
    await page.locator('#reset-password-link').click();
    await page.locator('#id_email').pressSequentially(randomEmail);
    await page.locator('input[value="Reset password"]').click();
    await expect(page.locator('div.message.success')).toContainText(`An e-mail has been sent to ${randomEmail}`);

    const href = await utils.getLinkInEmail(page, randomEmail, 'GPFWA: Password reset request');
    await page.goto(href, {waitUntil: 'load'});

    const newPassword = 'newpassword321';
    await page.locator('#id_new_password1').pressSequentially(newPassword);
    await page.locator('#id_new_password2').pressSequentially(newPassword);
    await page.locator('input[value="Reset password"]').click();

    await page.locator('#email').pressSequentially(randomEmail);
    await page.locator('#password').pressSequentially(newPassword);
    await page.locator('#login-container').getByRole('button', { name: 'Login' }).click();
    await page.waitForSelector('app-single-allele-annotation-wrapper', {timeout: 120000});
    await expect(page.locator('#user-data')).toBeVisible();
  });
});

test.describe('Logout tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/login', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
  });

  test('should stay logged out after refreshing the page', async({ page }) => {
    await page.locator('#logout-button').click();
    await page.waitForSelector('app-single-allele-annotation-wrapper', {timeout: 120000});
    await expect(page.locator('#login-button')).toBeVisible();
    await expect(page.locator('#register-button')).toBeVisible();
    await page.reload();
    await expect(page.locator('#login-button')).toBeVisible();
    await expect(page.locator('#register-button')).toBeVisible();
  });

  test('should stay logged out after clicking back button', async({ page }) => {
    await page.locator('#logout-button').click();
    await page.waitForSelector('#login-button', {timeout: 120000});
    await page.goBack();
    await expect(page.getByRole('button', { name: 'Login' })).toBeVisible();
  });
});