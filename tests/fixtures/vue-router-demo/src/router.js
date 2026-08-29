import { createRouter, createWebHistory } from 'vue-router';
import Home from './Home.vue';
import Settings from './Settings.vue';

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', component: Home },
    { path: '/settings', component: Settings }
  ]
});
