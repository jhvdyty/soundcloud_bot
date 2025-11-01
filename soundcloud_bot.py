"""
SoundCloud Auto-Liker Bot
Автоматически лайкает треки из репостов выбранных артистов и новые песни подписок
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Set
import logging

# Установить: pip install soundcloud-v2
try:
    from soundcloud import SoundCloud
except ImportError:
    print("Ошибка: библиотека soundcloud-v2 не установлена")
    print("Установите её командой: pip install soundcloud-v2")
    exit(1)

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('soundcloud_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)

class SoundCloudAutoLiker:
    def __init__(self, config_path: str = 'config.json'):
        """
        Инициализация бота
        
        Args:
            config_path: Путь к файлу конфигурации
        """
        self.config_path = config_path
        self.config = self.load_config()
        
        # Инициализация SoundCloud клиента
        try:
            auth_token = self.config.get('auth_token')
            if auth_token:
                self.client = SoundCloud(auth_token=auth_token)
                logging.info("SoundCloud клиент инициализирован с авторизацией")
            else:
                self.client = SoundCloud()
                logging.info("SoundCloud клиент инициализирован без авторизации")
        except Exception as e:
            logging.error(f"Ошибка инициализации клиента: {e}")
            self.client = SoundCloud()
        
        self.processed_tracks_file = 'processed_tracks.json'
        self.processed_tracks = self.load_processed_tracks()
        
        logging.info("SoundCloud Auto-Liker инициализирован")
    
    def load_config(self) -> Dict:
        """Загрузка конфигурации из файла"""
        default_config = {
            'auth_token': '',  # OAuth токен (опционально, для лайков)
            'your_username': 'your_soundcloud_username',
            'repost_artists': [
                # Список username артистов чьи репосты нужно лайкать
                'artist_username_1',
                'artist_username_2'
            ],
            'check_interval_minutes': 60,
            'hours_lookback': 72,  # Увеличено до 3 дней для первого запуска
            'dry_run': True,
            
            'features': {
                'auto_like_new_tracks': True,
                'auto_like_reposts': True,
                'auto_like_recommendations': False,
                'use_ml_filter': False
            },
            
            'filters': {
                'min_duration_seconds': 0,
                'max_duration_seconds': 0,  # 0 = без ограничения
                'genres': [],
                'min_likes': 0
            }
        }
        
        config_file = Path(self.config_path)
        
        if config_file.exists():
            with open(config_file, 'r', encoding='utf-8') as f:
                loaded_config = json.load(f)
                default_config.update(loaded_config)
                logging.info(f"Конфигурация загружена из {self.config_path}")
        else:
            with open(config_file, 'w', encoding='utf-8') as f:
                json.dump(default_config, f, indent=4, ensure_ascii=False)
            logging.warning(f"Создан новый файл конфигурации: {self.config_path}")
            logging.warning("Пожалуйста, отредактируйте config.json перед запуском")
        
        return default_config
    
    def load_processed_tracks(self) -> Set[int]:
        """Загрузка ID уже обработанных треков"""
        tracks_file = Path(self.processed_tracks_file)
        
        if tracks_file.exists():
            with open(tracks_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return set(data.get('track_ids', []))
        
        return set()
    
    def save_processed_tracks(self):
        """Сохранение ID обработанных треков"""
        with open(self.processed_tracks_file, 'w', encoding='utf-8') as f:
            json.dump({
                'track_ids': list(self.processed_tracks),
                'last_updated': datetime.now().isoformat()
            }, f, indent=4)
    
    def get_track_info(self, track_obj) -> Dict:
        """
        Извлечь информацию из объекта трека
        
        Args:
            track_obj: Объект трека от SoundCloud API
            
        Returns:
            Словарь с данными трека
        """
        try:
            # Попробуем получить атрибуты объекта
            track_dict = {}
            
            # Основные поля
            track_dict['id'] = getattr(track_obj, 'id', None)
            track_dict['title'] = getattr(track_obj, 'title', 'Unknown')
            track_dict['permalink_url'] = getattr(track_obj, 'permalink_url', '')
            track_dict['duration'] = getattr(track_obj, 'duration', 0)
            track_dict['genre'] = getattr(track_obj, 'genre', '')
            track_dict['likes_count'] = getattr(track_obj, 'likes_count', 0)
            track_dict['created_at'] = getattr(track_obj, 'created_at', '')
            
            # Информация о пользователе
            user = getattr(track_obj, 'user', None)
            if user:
                track_dict['user'] = {
                    'username': getattr(user, 'username', 'Unknown'),
                    'id': getattr(user, 'id', None)
                }
            else:
                track_dict['user'] = {'username': 'Unknown', 'id': None}
            
            return track_dict
        except Exception as e:
            logging.error(f"Ошибка при извлечении данных трека: {e}")
            return {}
    
    def get_new_tracks_from_followings(self) -> List[Dict]:
        """
        Получить новые треки от всех артистов на которых подписан
        
        Returns:
            Список новых треков
        """
        if not self.config['features']['auto_like_new_tracks']:
            return []
        
        logging.info("Проверка новых треков от подписок...")
        new_tracks = []
        
        try:
            username = self.config.get('your_username')
            
            if not username or username == 'your_soundcloud_username':
                logging.error("Не указан your_username в config.json")
                return []
            
            # Получаем информацию о пользователе
            user = self.client.resolve(f'https://soundcloud.com/{username}')
            
            # Получаем треки из стрима пользователя (это включает подписки)
            logging.info("Получение стрима (может занять время)...")
            stream_items = self.client.get_user_stream(user.id, limit=50)
            
            hours_back = int(self.config['hours_lookback'])
            cutoff_time = datetime.now() - timedelta(hours=hours_back)
            logging.info(f"Ищем треки новее чем: {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')} ({hours_back} часов назад)")
            
            total_items = 0
            for item in stream_items:
                total_items += 1
                try:
                    # В стриме могут быть разные типы объектов
                    track = getattr(item, 'track', None) or item
                    
                    if not hasattr(track, 'id'):
                        logging.debug(f"Элемент {total_items}: нет ID")
                        continue
                    
                    track_dict = self.get_track_info(track)
                    
                    if not track_dict or not track_dict.get('id'):
                        logging.debug(f"Элемент {total_items}: не удалось получить данные трека")
                        continue
                    
                    # Логируем каждый трек для первых 5 элементов
                    created_at = track_dict.get('created_at', 'unknown')
                    is_recent = self.is_track_recent(track_dict, cutoff_time)
                    is_processed = track_dict['id'] in self.processed_tracks
                    
                    if total_items <= 5:
                        logging.info(f"  → Трек: {track_dict['user']['username']} - {track_dict['title']}")
                        logging.info(f"     Created: {created_at} | Recent: {is_recent} | Processed: {is_processed}")
                    
                    # Проверяем что трек новый и еще не обработан
                    if track_dict['id'] not in self.processed_tracks and is_recent:
                        new_tracks.append(track_dict)
                        logging.info(f"✓ Найден новый трек: {track_dict['user']['username']} - {track_dict['title']}")
                
                except Exception as e:
                    logging.error(f"Ошибка при обработке элемента стрима #{total_items}: {e}")
                    continue
            
            logging.info(f"Обработано элементов стрима: {total_items}")
            logging.info(f"Найдено новых треков от подписок: {len(new_tracks)}")
            
        except Exception as e:
            logging.error(f"Ошибка при получении подписок: {e}")
        
        return new_tracks
    
    def get_reposts_from_selected_artists(self) -> List[Dict]:
        """
        Получить репосты от выбранных артистов
        
        Returns:
            Список треков из репостов
        """
        if not self.config['features']['auto_like_reposts']:
            return []
        
        logging.info("Проверка репостов выбранных артистов...")
        repost_tracks = []
        
        hours_back = int(self.config['hours_lookback'])
        cutoff_time = datetime.now() - timedelta(hours=hours_back)
        logging.info(f"Ищем треки новее чем: {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')} ({hours_back} часов назад)")
        
        for artist_username in self.config['repost_artists']:
            try:
                # Получаем информацию об артисте
                artist_url = f'https://soundcloud.com/{artist_username}'
                artist = self.client.resolve(artist_url)
                
                logging.info(f"Получение репостов от {artist_username} (ID: {artist.id})...")
                
                # Пробуем получить репосты напрямую
                reposts_list = []
                try:
                    # Некоторые версии библиотеки имеют метод get_user_reposts
                    reposts = self.client.get_user_reposts(artist.id, limit=20)
                    logging.info(f"  Используем метод get_user_reposts")
                    
                    # get_user_reposts возвращает объекты с полем track
                    for item in reposts:
                        track = getattr(item, 'track', item)
                        reposts_list.append(track)
                    
                    tracks_source = reposts_list
                    use_repost_method = True
                    
                except AttributeError:
                    # Если метода нет, используем get_user_tracks
                    logging.info(f"  Используем метод get_user_tracks")
                    tracks_source = self.client.get_user_tracks(artist.id, limit=20)
                    use_repost_method = False
                
                track_count = 0
                repost_count = 0
                
                for track in tracks_source:
                    track_count += 1
                    try:
                        track_dict = self.get_track_info(track)
                        
                        if not track_dict or not track_dict.get('id'):
                            continue
                        
                        # Если используем get_user_reposts - все треки уже репосты
                        # Если используем get_user_tracks - нужно проверять автора
                        if use_repost_method:
                            is_repost = True  # Все из get_user_reposts это репосты
                        else:
                            is_repost = track_dict['user']['id'] != artist.id
                        
                        is_recent = self.is_track_recent(track_dict, cutoff_time)
                        is_processed = track_dict['id'] in self.processed_tracks
                        
                        created_at = track_dict.get('created_at', 'unknown')
                        
                        # ВСЕГДА логируем первые 5 треков для отладки
                        if track_count <= 5:
                            logging.info(f"  Трек #{track_count}: {track_dict['user']['username']} - {track_dict['title']}")
                            logging.info(f"    Created: {created_at} | Is repost: {is_repost} | Recent: {is_recent} | Processed: {is_processed}")
                        
                        if is_repost:
                            repost_count += 1
                            if track_count > 5:  # Если не логировали выше
                                logging.info(f"  Репост #{repost_count}: {track_dict['user']['username']} - {track_dict['title']}")
                                logging.info(f"    Created: {created_at} | Recent: {is_recent} | Processed: {is_processed}")
                        
                        if (is_repost and 
                            track_dict['id'] not in self.processed_tracks and 
                            is_recent):
                            repost_tracks.append(track_dict)
                            logging.info(f"✓ Найден новый репост от {artist_username}: {track_dict['user']['username']} - {track_dict['title']}")
                    
                    except Exception as e:
                        logging.error(f"Ошибка при обработке трека #{track_count}: {e}")
                        continue
                
                logging.info(f"  Всего треков: {track_count}, из них репостов: {repost_count}")
                time.sleep(1)  # Задержка между артистами
                
            except Exception as e:
                logging.error(f"Ошибка при получении репостов {artist_username}: {e}")
                continue
        
        logging.info(f"Найдено треков из репостов: {len(repost_tracks)}")
        return repost_tracks
    
    def test_show_followings(self):
        """Показать список подписок для отладки"""
        try:
            username = self.config.get('your_username')
            if not username or username == 'your_soundcloud_username':
                logging.error("Не указан your_username в config.json")
                return
            
            user = self.client.resolve(f'https://soundcloud.com/{username}')
            logging.info(f"\n{'='*60}")
            logging.info(f"Пользователь: {username} (ID: {user.id})")
            logging.info(f"{'='*60}")
            
            # Пробуем разные методы получения подписок
            methods_tried = []
            
            # Метод 1: get_user_followings
            try:
                logging.info("Пробуем метод: get_user_followings...")
                followings = list(self.client.get_user_followings(user.id, limit=20))
                methods_tried.append(('get_user_followings', len(followings)))
                
                if followings:
                    logging.info(f"\n✓ Найдено подписок: {len(followings)}")
                    for i, artist in enumerate(followings[:20], 1):
                        artist_name = getattr(artist, 'username', 'Unknown')
                        artist_id = getattr(artist, 'id', 'Unknown')
                        logging.info(f"  {i}. {artist_name} (ID: {artist_id})")
                    return
            except AttributeError as e:
                logging.warning(f"Метод get_user_followings не доступен: {e}")
                methods_tried.append(('get_user_followings', 'не доступен'))
            except Exception as e:
                logging.error(f"Ошибка get_user_followings: {e}")
                methods_tried.append(('get_user_followings', f'ошибка: {e}'))
            
            # Метод 2: Через стрим
            try:
                logging.info("\nПробуем получить подписки через стрим...")
                stream = list(self.client.get_user_stream(user.id, limit=200))
                methods_tried.append(('get_user_stream', len(stream)))
                
                logging.info(f"Получено элементов стрима: {len(stream)}")
                
                if len(stream) < 20:
                    logging.warning(f"⚠ Стрим возвращает мало элементов ({len(stream)}). Возможно приватный профиль или мало активности.")
                
                # Извлекаем уникальных артистов из стрима
                artists = {}
                for item in stream:
                    track = getattr(item, 'track', item)
                    if hasattr(track, 'user'):
                        user_obj = track.user
                        artist_id = getattr(user_obj, 'id', None)
                        if artist_id and artist_id != user.id:
                            artists[artist_id] = getattr(user_obj, 'username', 'Unknown')
                
                if artists:
                    logging.info(f"\n✓ Найдено уникальных артистов в стриме: {len(artists)}")
                    for i, (artist_id, artist_name) in enumerate(list(artists.items())[:30], 1):
                        logging.info(f"  {i}. {artist_name} (ID: {artist_id})")
                    
                    if len(artists) > 30:
                        logging.info(f"  ... и ещё {len(artists) - 30} артистов")
                        
            except Exception as e:
                logging.error(f"Ошибка при получении стрима: {e}")
                methods_tried.append(('get_user_stream', f'ошибка: {e}'))
            
            # Метод 3: Через лайки пользователя
            try:
                logging.info("\nПробуем получить подписки через лайки...")
                likes = list(self.client.get_user_likes(user.id, limit=200))
                methods_tried.append(('get_user_likes', len(likes)))
                
                logging.info(f"Получено лайков: {len(likes)}")
                
                # Извлекаем уникальных артистов из лайкнутых треков
                artists_from_likes = {}
                for track in likes:
                    if hasattr(track, 'user'):
                        user_obj = track.user
                        artist_id = getattr(user_obj, 'id', None)
                        if artist_id and artist_id != user.id:
                            artists_from_likes[artist_id] = getattr(user_obj, 'username', 'Unknown')
                
                if artists_from_likes:
                    logging.info(f"\n✓ Найдено уникальных артистов в лайках: {len(artists_from_likes)}")
                    for i, (artist_id, artist_name) in enumerate(list(artists_from_likes.items())[:30], 1):
                        logging.info(f"  {i}. {artist_name} (ID: {artist_id})")
                    
                    if len(artists_from_likes) > 30:
                        logging.info(f"  ... и ещё {len(artists_from_likes) - 30} артистов")
                    return
                        
            except Exception as e:
                logging.error(f"Ошибка при получении лайков: {e}")
                methods_tried.append(('get_user_likes', f'ошибка: {e}'))
            
            # Метод 4: Просто показываем артистов из config.json
            if self.config['repost_artists']:
                logging.info(f"\n📋 Артисты из config.json (repost_artists): {len(self.config['repost_artists'])}")
                for i, artist_name in enumerate(self.config['repost_artists'], 1):
                    logging.info(f"  {i}. {artist_name}")
                logging.info("\n💡 Совет: Бот будет проверять новые треки и репосты от этих артистов")
                return
            
            # Итог
            logging.warning(f"\nНе удалось получить подписки. Попробованные методы:")
            for method, result in methods_tried:
                logging.warning(f"  - {method}: {result}")
            
        except Exception as e:
            logging.error(f"Ошибка при получении подписок: {e}")
    
    def test_show_user_reposts(self, username: str):
        """Показать репосты конкретного пользователя для отладки"""
        try:
            artist_url = f'https://soundcloud.com/{username}'
            artist = self.client.resolve(artist_url)
            
            logging.info(f"\n{'='*60}")
            logging.info(f"Репосты пользователя: {username}")
            logging.info(f"{'='*60}")
            
            reposts = list(self.client.get_user_reposts(artist.id, limit=10))
            logging.info(f"Найдено репостов: {len(reposts)}\n")
            
            for i, item in enumerate(reposts[:10], 1):
                # Репост может быть обёрнут в объект
                track = getattr(item, 'track', item)
                track_dict = self.get_track_info(track)
                
                if track_dict:
                    logging.info(f"{i}. {track_dict['user']['username']} - {track_dict['title']}")
                    logging.info(f"   Created: {track_dict.get('created_at', 'unknown')}")
                    logging.info(f"   URL: {track_dict.get('permalink_url', '')}\n")
                else:
                    logging.info(f"{i}. [Не удалось получить информацию]\n")
                    
        except Exception as e:
            logging.error(f"Ошибка: {e}")
    
        """
        Получить репосты от выбранных артистов
        
        Returns:
            Список треков из репостов
        """
        if not self.config['features']['auto_like_reposts']:
            return []
        
        logging.info("Проверка репостов выбранных артистов...")
        repost_tracks = []
        
        hours_back = int(self.config['hours_lookback'])
        cutoff_time = datetime.now() - timedelta(hours=hours_back)
        logging.info(f"Ищем треки новее чем: {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')} ({hours_back} часов назад)")
        
        for artist_username in self.config['repost_artists']:
            try:
                # Получаем информацию об артисте
                artist_url = f'https://soundcloud.com/{artist_username}'
                artist = self.client.resolve(artist_url)
                
                logging.info(f"Получение репостов от {artist_username} (ID: {artist.id})...")
                
                # Пробуем получить репосты напрямую
                reposts_list = []
                try:
                    # Некоторые версии библиотеки имеют метод get_user_reposts
                    reposts = self.client.get_user_reposts(artist.id, limit=20)
                    logging.info(f"  Используем метод get_user_reposts")
                    
                    # get_user_reposts возвращает объекты с полем track
                    for item in reposts:
                        track = getattr(item, 'track', item)
                        reposts_list.append(track)
                    
                    tracks_source = reposts_list
                    use_repost_method = True
                    
                except AttributeError:
                    # Если метода нет, используем get_user_tracks
                    logging.info(f"  Используем метод get_user_tracks")
                    tracks_source = self.client.get_user_tracks(artist.id, limit=20)
                    use_repost_method = False
                
                track_count = 0
                repost_count = 0
                
                for track in tracks_source:
                    track_count += 1
                    try:
                        track_dict = self.get_track_info(track)
                        
                        if not track_dict or not track_dict.get('id'):
                            continue
                        
                        # Если используем get_user_reposts - все треки уже репосты
                        # Если используем get_user_tracks - нужно проверять автора
                        if use_repost_method:
                            is_repost = True  # Все из get_user_reposts это репосты
                        else:
                            is_repost = track_dict['user']['id'] != artist.id
                        
                        is_recent = self.is_track_recent(track_dict, cutoff_time)
                        is_processed = track_dict['id'] in self.processed_tracks
                        
                        created_at = track_dict.get('created_at', 'unknown')
                        
                        # ВСЕГДА логируем первые 5 треков для отладки
                        if track_count <= 5:
                            logging.info(f"  Трек #{track_count}: {track_dict['user']['username']} - {track_dict['title']}")
                            logging.info(f"    Created: {created_at} | Is repost: {is_repost} | Recent: {is_recent} | Processed: {is_processed}")
                        
                        if is_repost:
                            repost_count += 1
                            if track_count > 5:  # Если не логировали выше
                                logging.info(f"  Репост #{repost_count}: {track_dict['user']['username']} - {track_dict['title']}")
                                logging.info(f"    Created: {created_at} | Recent: {is_recent} | Processed: {is_processed}")
                        
                        if (is_repost and 
                            track_dict['id'] not in self.processed_tracks and 
                            is_recent):
                            repost_tracks.append(track_dict)
                            logging.info(f"✓ Найден новый репост от {artist_username}: {track_dict['user']['username']} - {track_dict['title']}")
                    
                    except Exception as e:
                        logging.error(f"Ошибка при обработке трека #{track_count}: {e}")
                        continue
                
                logging.info(f"  Всего треков: {track_count}, из них репостов: {repost_count}")
                time.sleep(1)  # Задержка между артистами
                
            except Exception as e:
                logging.error(f"Ошибка при получении репостов {artist_username}: {e}")
                continue
        
        logging.info(f"Найдено треков из репостов: {len(repost_tracks)}")
        return repost_tracks
    
    def is_track_recent(self, track: Dict, cutoff_time: datetime) -> bool:
        """
        Проверить что трек недавний
        
        Args:
            track: Словарь с данными трека
            cutoff_time: Время отсечки
            
        Returns:
            True если трек новый
        """
        try:
            created_at = track.get('created_at', '')
            if not created_at:
                return True
            
            # Парсим дату
            # Формат может быть: 2024-10-30T12:34:56Z
            created_at = created_at.replace('Z', '+00:00')
            if '+00:00' in created_at:
                track_time = datetime.fromisoformat(created_at)
            else:
                track_time = datetime.fromisoformat(created_at)
            
            # Убираем timezone для сравнения
            track_time = track_time.replace(tzinfo=None)
            
            return track_time > cutoff_time
        except Exception as e:
            logging.debug(f"Не удалось определить время трека: {e}")
            return True
    
    def apply_filters(self, track: Dict) -> bool:
        """
        Применить фильтры к треку
        
        Args:
            track: Словарь с данными трека
            
        Returns:
            True если трек проходит фильтры
        """
        filters = self.config['filters']
        
        # Проверка длительности
        duration_ms = track.get('duration', 0)
        duration_seconds = duration_ms / 1000
        
        if filters['min_duration_seconds'] > 0 and duration_seconds < filters['min_duration_seconds']:
            return False
        
        if filters['max_duration_seconds'] > 0 and duration_seconds > filters['max_duration_seconds']:
            return False
        
        # Проверка жанра
        genre = track.get('genre', '').lower()
        filter_genres = [g.lower() for g in filters['genres']]
        if filter_genres and genre not in filter_genres:
            return False
        
        # Проверка минимального количества лайков
        likes_count = track.get('likes_count', 0)
        if likes_count < filters['min_likes']:
            return False
        
        return True
    
    def like_track(self, track: Dict) -> bool:
        """
        Лайкнуть трек
        
        Args:
            track: Словарь с данными трека
            
        Returns:
            True если успешно
        """
        track_title = track.get('title', 'Unknown')
        artist_name = track.get('user', {}).get('username', 'Unknown')
        permalink_url = track.get('permalink_url', '')
        duration = track.get('duration', 0) / 1000  # в секундах
        likes = track.get('likes_count', 0)
        
        if self.config['dry_run']:
            logging.info(f"[DRY RUN] Лайкнули бы: {artist_name} - {track_title}")
            logging.info(f"           Длительность: {int(duration//60)}:{int(duration%60):02d} | Лайков: {likes}")
            logging.info(f"           URL: {permalink_url}")
            return True
        
        # Попытка реального лайка
        try:
            track_id = track.get('id')
            if not track_id:
                logging.error("Не удалось получить ID трека")
                return False
            
            # Для реального лайка нужен auth_token и метод API
            if not self.config.get('auth_token'):
                logging.error("Для реальных лайков нужен auth_token в config.json")
                return False
            
            # Метод лайка (может отличаться в зависимости от версии библиотеки)
            self.client.like_track(track_id)
            logging.info(f"✓ Лайкнули: {artist_name} - {track_title}")
            return True
            
        except Exception as e:
            logging.error(f"Ошибка при лайке трека: {e}")
            return False
    
    def process_tracks(self, tracks: List[Dict]):
        """
        Обработать список треков
        
        Args:
            tracks: Список треков для обработки
        """
        liked_count = 0
        filtered_count = 0
        
        for track in tracks:
            try:
                # Применяем фильтры
                if not self.apply_filters(track):
                    filtered_count += 1
                    track_id = track.get('id')
                    if track_id:
                        self.processed_tracks.add(track_id)
                    continue
                
                # Лайкаем трек
                if self.like_track(track):
                    liked_count += 1
                    track_id = track.get('id')
                    if track_id:
                        self.processed_tracks.add(track_id)
                
                time.sleep(1)  # Задержка между лайками
                
            except Exception as e:
                track_title = track.get('title', 'Unknown')
                logging.error(f"Ошибка при обработке трека {track_title}: {e}")
        
        if filtered_count > 0:
            logging.info(f"Отфильтровано треков: {filtered_count}")
        logging.info(f"Обработано треков: {liked_count}")
        self.save_processed_tracks()
    
    def run_once(self):
        """Один цикл проверки и обработки треков"""
        logging.info("=" * 50)
        logging.info("Начало цикла проверки")
        
        all_tracks = []
        
        # Получаем новые треки от подписок
        new_tracks = self.get_new_tracks_from_followings()
        all_tracks.extend(new_tracks)
        
        # Получаем репосты выбранных артистов
        repost_tracks = self.get_reposts_from_selected_artists()
        all_tracks.extend(repost_tracks)
        
        # Удаляем дубликаты
        unique_tracks = {}
        for track in all_tracks:
            track_id = track.get('id')
            if track_id and track_id not in unique_tracks:
                unique_tracks[track_id] = track
        
        logging.info(f"Всего уникальных треков для обработки: {len(unique_tracks)}")
        
        # Обрабатываем треки
        if unique_tracks:
            self.process_tracks(list(unique_tracks.values()))
        else:
            logging.info("Новых треков не найдено")
        
        logging.info("Цикл завершен")
    
    def run(self):
        """Основной цикл работы бота"""
        logging.info("Бот запущен")
        logging.info(f"Интервал проверки: {self.config['check_interval_minutes']} минут")
        logging.info(f"Режим dry_run: {self.config['dry_run']}")
        
        try:
            while True:
                self.run_once()
                
                # Ждем до следующей проверки
                wait_seconds = self.config['check_interval_minutes'] * 60
                logging.info(f"Ожидание {self.config['check_interval_minutes']} минут до следующей проверки...")
                time.sleep(wait_seconds)
                
        except KeyboardInterrupt:
            logging.info("\nБот остановлен пользователем")
        except Exception as e:
            logging.error(f"Критическая ошибка: {e}")
            raise


def main():
    """Точка входа"""
    print("=" * 60)
    print("SoundCloud Auto-Liker Bot")
    print("=" * 60)
    
    bot = SoundCloudAutoLiker()
    
    print("\nВыберите режим работы:")
    print("1. Разовый запуск (один раз)")
    print("2. Постоянная работа (цикл)")
    print("3. Тест: показать подписки")
    print("4. Тест: показать репосты пользователя")
    print("5. Выход")
    
    choice = input("\nВведите номер (1-5): ").strip()
    
    if choice == '1':
        print("\nЗапуск разовой проверки...\n")
        bot.run_once()
        print("\nГотово!")
    elif choice == '2':
        print("\nЗапуск в режиме постоянной работы...")
        print("Для остановки нажмите Ctrl+C\n")
        bot.run()
    elif choice == '3':
        print("\nПолучение списка подписок...\n")
        bot.test_show_followings()
        print("\nГотово!")
    elif choice == '4':
        username = input("Введите username пользователя: ").strip()
        if username:
            print(f"\nПолучение репостов {username}...\n")
            bot.test_show_user_reposts(username)
            print("\nГотово!")
        else:
            print("Username не указан")
    else:
        print("Выход...")


if __name__ == '__main__':
    main()